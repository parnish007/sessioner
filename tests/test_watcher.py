from datetime import datetime, timezone, timedelta
import json

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

