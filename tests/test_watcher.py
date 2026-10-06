from datetime import datetime, timezone, timedelta
import json
import threading

import pytest

from test_cli import Engine, row, service


NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def stamp(hours):
    return (NOW + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


def usage(pct, *, reset=None):
    window = {"pct": pct}
    if reset is not None:
        window["resetsAt"] = stamp(reset)
    return {"fiveHour": window, "sevenDay": {"pct": pct, **({"resetsAt": stamp(reset)} if reset is not None else {})}}


def make_watcher(tmp_path, accounts, *, login="active@example.com", now=NOW.timestamp()):
    from sessioner.watcher import ResetWatcher

    engine = Engine(accounts, login=login)
    svc = service(tmp_path, engine)
    clock = lambda: now
    watcher = ResetWatcher(svc, state_path=tmp_path / "watcher.json", clock=clock)
    return watcher, svc, engine


def test_watcher_is_off_until_explicitly_enabled(tmp_path):
    watcher, _, engine = make_watcher(tmp_path, [
        row(1, "active@example.com", usage=usage(100)),
        row(2, "backup@example.com", usage=usage(10)),
    ])
    result = watcher.tick()
    assert result["state"] == "off"
    assert not [call for call in engine.calls if call[0] == "switch"]


def test_enabled_watcher_switches_only_an_exhausted_active_account(tmp_path):
    watcher, _, engine = make_watcher(tmp_path, [
        row(1, "active@example.com", usage=usage(100)),
        row(2, "backup@example.com", usage=usage(10)),
    ])
    watcher.set_enabled(True)
    result = watcher.tick()
    assert result["state"] == "switched"
    assert result["lastSwitchTo"] == 2
    assert ("switch", "2") in engine.calls

    result = watcher.tick()
    assert result["state"] == "armed"
    assert [call for call in engine.calls if call[0] == "switch"] == [("switch", "2")]


def test_watcher_waits_for_known_earliest_reset_when_all_accounts_are_exhausted(tmp_path):
    watcher, _, _ = make_watcher(tmp_path, [
        row(1, "active@example.com", usage=usage(100, reset=8)),
        row(2, "backup@example.com", usage=usage(100, reset=2)),
    ])
    watcher.set_enabled(True)
    result = watcher.tick()
    assert result["state"] == "waiting"
    assert result["reason"] == "all-exhausted"
    assert result["nextPollAt"] == pytest.approx((NOW + timedelta(hours=2)).timestamp())


def test_watcher_backs_off_when_reset_timing_is_unknown(tmp_path):
    watcher, _, _ = make_watcher(tmp_path, [
        row(1, "active@example.com", usage=usage(100)),
        row(2, "backup@example.com", usage=usage(100)),
    ])
    watcher.set_enabled(True)
    result = watcher.tick(interval=45)
    assert result["state"] == "reset_unknown"
    assert result["reason"] == "reset-unknown"
    assert result["nextPollAt"] == pytest.approx(NOW.timestamp() + 45)


def test_watcher_persists_only_non_secret_state(tmp_path):
    watcher, _, _ = make_watcher(tmp_path, [
        row(1, "active@example.com", usage=usage(100)),
        row(2, "backup@example.com", usage=usage(10)),
    ])
    watcher.set_enabled(True)
    watcher.tick()
    saved = json.loads((tmp_path / "watcher.json").read_text())
    assert set(saved) >= {"enabled", "state", "lastPollAt", "nextPollAt"}
    assert "accessToken" not in json.dumps(saved)
    assert "credentials" not in json.dumps(saved).lower()


def test_concurrent_watchers_do_not_refresh_or_switch_from_the_same_snapshot(tmp_path):
    from sessioner.watcher import ResetWatcher

    entered, release = threading.Event(), threading.Event()

    class SlowFirstRefresh(Engine):
        refreshes = 0

        def list_accounts(self, *, json_output, fetch=None):
            payload = super().list_accounts(json_output=json_output, fetch=fetch)
            if fetch is None:
                self.refreshes += 1
                if self.refreshes == 1:
                    entered.set()
                    assert release.wait(5)
            return payload

    engine = SlowFirstRefresh([
        row(1, "active@example.com", usage=usage(100)),
        row(2, "backup@example.com", usage=usage(10)),
        row(3, "other@example.com", usage=usage(20)),
    ], login="active@example.com")
    svc = service(tmp_path, engine)
    first = ResetWatcher(svc, state_path=tmp_path / "watcher.json")
    second = ResetWatcher(svc, state_path=tmp_path / "watcher.json")
    first.set_enabled(True)
    worker = threading.Thread(target=first.tick)
    worker.start()
    try:
        assert entered.wait(3)
        second.tick()
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    assert engine.refreshes == 1
    assert [call for call in engine.calls if call[0] == "switch"] == [("switch", "2")]
    assert engine.login == "backup@example.com"


def test_watcher_stops_when_another_controller_has_already_selected_its_candidate(tmp_path):
    from sessioner.watcher import ResetWatcher

    class SelectedElsewhere(Engine):
        def switch_to(self, identifier, *, json_output):
            self.login = next(account["email"] for account in self.rows if str(account["number"]) == identifier)
            return super().switch_to(identifier, json_output=json_output)

    engine = SelectedElsewhere([
        row(1, "active@example.com", usage=usage(100)),
        row(2, "backup@example.com", usage=usage(10)),
        row(3, "other@example.com", usage=usage(20)),
    ], login="active@example.com")
    svc = service(tmp_path, engine)
    watcher = ResetWatcher(svc, state_path=tmp_path / "watcher.json")
    watcher.set_enabled(True)
    state = watcher.tick()
    assert [call for call in engine.calls if call[0] == "switch"] == [("switch", "2")]
    assert state["state"] == "armed"
    assert not [item for item in svc.activity()["items"] if item["kind"] in {"switch_failed", "switch_confirmed"}]
