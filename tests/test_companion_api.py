"""Desktop setup checks and privacy controls through the real local API."""

import json
import threading

import pytest

from test_cli import Engine, product_module, row, service
from test_web import USAGE, running


def ready_service(tmp_path):
    return service(tmp_path, Engine([
        row(1, "one@example.test", "Primary", usage=USAGE),
        row(2, "two@example.test", "Backup", usage=USAGE),
    ], login="one@example.test"))


def checks(state):
    return {item["id"]: item for item in state["health"]["checks"]}


def test_state_has_safe_preferences_activity_and_actual_watcher_health(tmp_path):
    api = product_module("sessioner.web.api")
    svc = ready_service(tmp_path)
    svc.watcher().set_enabled(True)
    state = api.build_state(svc)
    assert state["preferences"] == {"statisticsEnabled": True, "notificationsEnabled": True}
    assert state["activity"] == {"items": []}
    assert state["desktop"]["running"] is False
    assert state["watcher"]["enabled"] is True
    assert state["health"]["watcherRunning"] is False
    assert checks(state)["watcher"]["status"] == "attention"
    assert checks(state)["watcher"]["fix"]["command"].endswith("desktop")
    assert not state["health"]["ready"]  # the automatic hook is not configured yet


def test_privacy_api_skips_even_the_session_metadata_boundary(tmp_path, monkeypatch):
    api = product_module("sessioner.web.api")
    svc = ready_service(tmp_path)
    result = api.act(svc, "/api/preferences", {"statisticsEnabled": False})
    assert result["state"]["preferences"]["statisticsEnabled"] is False

    def forbidden(*args, **kwargs):
        pytest.fail("Account-only privacy mode entered the statistics boundary")

    monkeypatch.setattr(svc, "live_sessions", forbidden)
    monkeypatch.setattr(product_module("sessioner.tokens"), "TokenLedger", forbidden)
    state = api.build_state(svc)
    assert state["sessions"]["items"] == []
    assert state["sessions"]["enabled"] is False
    accounts, live_ids = api.token_inputs(svc)
    report = api.build_tokens(svc, accounts, live_ids)["tokens"]
    assert report["available"] is False and report["reason"] == "statistics-disabled"
    assert report["sessions"] == [] and report["totals"]["total"] == 0
    assert state["backupReady"] is True


@pytest.mark.parametrize("body", [
    {}, [], None, {"statisticsEnabled": 1}, {"notificationsEnabled": "yes"},
    {"statisticsEnabled": False, "path": "other-profile"},
])
def test_preference_input_is_strict_and_has_no_side_effect(tmp_path, body):
    api = product_module("sessioner.web.api")
    svc = ready_service(tmp_path)
    with pytest.raises(product_module("sessioner.service").SessionerError):
        api.act(svc, "/api/preferences", body)
    assert svc.preferences().read()["statisticsEnabled"] is True
    assert not [call for call in svc.switcher.calls if call[0] == "switch"]


def test_health_explains_configuration_exhaustion_and_safe_fixes(tmp_path):
    api = product_module("sessioner.web.api")
    svc = ready_service(tmp_path)
    state = api.act(svc, "/api/health/fix", {"check": "automatic"})["state"]
    assert checks(state)["automatic"]["status"] == "ok"
    assert state["health"]["ready"] is True
    for account in svc.switcher.rows:
        account["usage"] = {"fiveHour": {"pct": 100, "resetsAt": "2099-10-06T12:00:00Z"}}
    state = api.build_state(svc)
    assert checks(state)["backup"]["status"] == "attention"
    assert "reset" in checks(state)["backup"]["message"].lower()
    assert state["health"]["ready"] is False
    assert not [call for call in svc.switcher.calls if call[0] == "switch"]


@pytest.mark.parametrize("body", [
    {}, [], {"check": "run-command"}, {"check": "watcher", "command": "anything"},
])
def test_health_fix_rejects_unknown_or_extra_actions(tmp_path, body):
    api = product_module("sessioner.web.api")
    svc = ready_service(tmp_path)
    with pytest.raises(product_module("sessioner.service").SessionerError):
        api.act(svc, "/api/health/fix", body)
    assert svc.watcher().status()["enabled"] is False


def test_desktop_liveness_controls_health_and_watcher_enable(tmp_path, monkeypatch):
    api = product_module("sessioner.web.api")
    desktop = product_module("sessioner.desktop")
    svc = ready_service(tmp_path)
    monkeypatch.setattr(desktop, "status_for", lambda _: {
        "running": True, "watcherRunning": False, "pid": 100, "heartbeatAt": 1780000000,
    })
    wakes = []
    monkeypatch.setattr(desktop, "wake_for", lambda directory: wakes.append(directory))
    result = api.act(svc, "/api/health/fix", {"check": "watcher"})
    assert result["state"]["watcher"]["enabled"] is True
    assert wakes == [svc.state_dir]
    assert result["state"]["health"]["watcherRunning"] is False
    monkeypatch.setattr(desktop, "status_for", lambda _: {
        "running": True, "watcherRunning": True, "pid": 100, "heartbeatAt": 1780000000,
    })
    state = api.build_state(svc)
    assert state["health"]["watcherRunning"] is True
    assert checks(state)["watcher"]["status"] == "ok"


def test_new_routes_keep_launch_authentication_and_origin_guards(running):
    client, _ = running
    for route, body in (("/api/preferences", {"statisticsEnabled": False}),
                        ("/api/health/fix", {"check": "automatic"})):
        assert client.request("POST", route, body=body, token=False)[0] == 401
        assert client.request("POST", route, body=body,
                              headers={"Origin": "https://elsewhere.test"})[0] == 403
    status, data = client.request("POST", "/api/preferences", body={"statisticsEnabled": False})
    assert status == 200
    assert json.loads(data)["state"]["sessions"]["enabled"] is False
    status, data = client.request("GET", "/api/tokens")
    assert status == 200 and json.loads(data)["tokens"]["reason"] == "statistics-disabled"


def test_dashboard_quit_notifies_desktop_owner_after_reply(running):
    client, _ = running
    called = threading.Event()
    client.server.on_quit = called.set
    status, _ = client.request("POST", "/api/quit", body={})
    assert status == 200
    assert called.wait(1), "The desktop owner must also stop its watcher and tray"


def test_non_windows_health_offers_the_terminal_watcher(tmp_path, monkeypatch):
    api = product_module("sessioner.web.api")
    desktop = product_module("sessioner.desktop")
    monkeypatch.setattr(desktop, "supported", lambda: False)
    svc = ready_service(tmp_path)
    svc.watcher().set_enabled(True)
    state = api.build_state(svc)
    assert checks(state)["watcher"]["fix"]["command"].endswith("watch")
