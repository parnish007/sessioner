"""Details drawers: candidate ranking, watcher wiring, and the watch command."""

from datetime import datetime, timedelta, timezone

import pytest

from sessioner.accounts.selection import select_backup
from test_cli import Engine, invoke, product_module, row, service
from test_watcher import usage

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def spent(pct=100):
    return {"fiveHour": {"pct": pct}, "sevenDay": {"pct": pct}}


def accounts():
    return [
        row(1, "active@example.com", "work", usage=spent()),
        row(2, "b@example.com", "mid", usage=usage(50)),
        row(3, "c@example.com", "roomy", usage=usage(20)),
        row(4, "d@example.com", "off", disabled=True, usage=usage(5)),
    ]


# ---- ranking ----

def test_selection_lists_candidates_in_the_order_the_hook_would_try_them():
    result = select_backup(accounts(), active_number=1, now=NOW)
    assert result.target == 3
    assert result.ranked == (3, 2)


def test_ranking_is_empty_when_nothing_can_be_chosen():
    result = select_backup([row(1, "a@x.com", usage=spent()), row(2, "b@x.com", usage=spent())], active_number=1, now=NOW)
    assert result.target is None and result.ranked == ()


def test_the_browser_shows_the_same_ranking_the_policy_produces(tmp_path):
    api = product_module("sessioner.web.api")
    state = api.build_state(service(tmp_path, Engine(accounts(), login="active@example.com")))
    assert state["selection"]["nextAccount"] == 3
    assert state["selection"]["ranked"] == [3, 2]


# ---- watcher in the browser state ----

def test_state_reports_a_disabled_watcher_and_how_to_start_it(tmp_path):
    api = product_module("sessioner.web.api")
    watcher = api.build_state(service(tmp_path, Engine(accounts(), login="active@example.com")))["watcher"]
    assert watcher["enabled"] is False and watcher["state"] == "off"
    assert watcher["command"].endswith("watch")


def test_opening_the_page_never_starts_the_watcher_or_changes_accounts(tmp_path):
    api = product_module("sessioner.web.api")
    engine = Engine(accounts(), login="active@example.com")
    svc = service(tmp_path, engine)
    svc.watcher().set_enabled(True)
    for _ in range(3):
        state = api.build_state(svc)
    assert state["watcher"]["enabled"] is True
    assert not [call for call in engine.calls if call[0] == "switch"]


def test_watcher_toggle_persists_only_the_setting(tmp_path):
    api = product_module("sessioner.web.api")
    engine = Engine(accounts(), login="active@example.com")
    svc = service(tmp_path, engine)
    result = api.act(svc, "/api/watcher", {"enabled": True})
    assert result["state"]["watcher"]["enabled"] is True
    assert "sessioner" in result["message"].lower() and "watch" in result["message"]
    assert not [call for call in engine.calls if call[0] == "switch"]
    assert api.act(svc, "/api/watcher", {"enabled": False})["state"]["watcher"]["enabled"] is False


def test_watcher_toggle_rejects_anything_but_a_boolean(tmp_path):
    api = product_module("sessioner.web.api")
    svc = service(tmp_path, Engine(accounts(), login="active@example.com"))
    for body in ({}, {"enabled": "yes"}, None):
        with pytest.raises(product_module("sessioner.service").SessionerError):
            api.act(svc, "/api/watcher", body)


# ---- sessioner watch ----

def test_watch_once_explains_itself_when_the_watcher_is_off(tmp_path, monkeypatch):
    engine = Engine(accounts(), login="active@example.com")
    code, text = invoke(monkeypatch, ["watch", "--once"], service(tmp_path, engine))
    assert code == 0
    assert "off" in text.lower()
    assert not [call for call in engine.calls if call[0] == "switch"]


def test_watch_once_switches_an_exhausted_account_when_enabled(tmp_path, monkeypatch):
    engine = Engine(accounts(), login="active@example.com")
    svc = service(tmp_path, engine)
    svc.watcher().set_enabled(True)
    code, text = invoke(monkeypatch, ["watch", "--once"], svc)
    assert code == 0
    assert ("switch", "3") in engine.calls
    assert "switched" in text.lower()
    assert "retry" in text.lower()  # Claude owns retry; the output says so


def test_watch_rejects_a_nonpositive_interval(tmp_path, monkeypatch):
    code, text = invoke(monkeypatch, ["watch", "--once", "--interval", "0"], service(tmp_path, Engine(accounts(), login="active@example.com")))
    assert code == 1 and "greater than zero" in text


def test_the_loop_reports_every_tick_and_stops_when_the_watcher_is_turned_off(tmp_path):
    from sessioner.watcher import ResetWatcher

    svc = service(tmp_path, Engine(accounts(), login="active@example.com"))
    clock = [NOW.timestamp()]
    seen = []

    def sleeper(delay):
        clock[0] += delay
        if len(seen) == 2:
            watcher.set_enabled(False)

    watcher = ResetWatcher(svc, state_path=tmp_path / "w.json", clock=lambda: clock[0], sleeper=sleeper)
    watcher.set_enabled(True)
    assert watcher.run(interval=30, on_tick=lambda state: seen.append(state["state"])) == 0
    assert seen[0] == "switched" and seen[-1] == "off"
