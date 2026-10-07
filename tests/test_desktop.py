"""Desktop lifecycle, actual liveness and account operation isolation."""

import importlib
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest


def desktop_module():
    try:
        return importlib.import_module("sessioner.desktop")
    except ModuleNotFoundError:
        pytest.fail("The Sessioner desktop companion is not implemented")


class Preferences:
    def read(self):
        return {"statisticsEnabled": True, "notificationsEnabled": True}


class Watcher:
    def __init__(self):
        self.enabled = False
        self.ticks = 0
        self.next_poll = None
        self.on_tick = None

    def status(self):
        return {"enabled": self.enabled, "state": "armed" if self.enabled else "off", "nextPollAt": self.next_poll, "running": False}

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.next_poll = None
        return self.status()

    def tick(self, *, interval=60, cancelled=None):
        self.ticks += 1
        self.next_poll = time.time() + interval
        if self.on_tick:
            self.on_tick()
        return self.status()


class Service:
    def __init__(self, state_dir):
        self.state_dir = state_dir
        self.reset_watcher = Watcher()
        self.rows = [
            {"number": 1, "email": "primary@example.com", "alias": "Primary", "usageStatus": "ok", "usageAgeSeconds": 5, "usage": {"fiveHour": {"pct": 20}}},
            {"number": 2, "email": "backup@example.com", "alias": "Backup", "usageStatus": "ok", "usageAgeSeconds": 5, "usage": {"fiveHour": {"pct": 10}}},
        ]
        self.active_number = 1
        self.refreshes = 0
        self.inside_switch = None
        self.finish_switch = None
        self.events = []
        self.on_snapshot = None

    def snapshot(self, *, refresh=False, source="manual", force=False):
        if refresh:
            self.refreshes += 1
        if force:
            self.forced = getattr(self, "forced", 0) + 1
        if self.on_snapshot:
            self.on_snapshot()
        return SimpleNamespace(accounts=self.rows, active=next(row for row in self.rows if row["number"] == self.active_number))

    def watcher(self):
        return self.reset_watcher

    def preferences(self):
        return Preferences()

    def activity(self, limit=50):
        return {"items": self.events[:limit]}

    def switch(self, target, *, source="manual"):
        if self.inside_switch:
            self.inside_switch.set()
            assert self.finish_switch.wait(2)
        self.active_number = int(target)
        return self.rows[int(target) - 1], True


class Tray:
    def __init__(self):
        self.menus = []
        self.messages = []
        self.stopped = threading.Event()

    def update(self, title, menu):
        self.menus.append((title, menu))

    def notify(self, title, body):
        self.messages.append((title, body))

    def set_notifications_enabled(self, enabled):
        self.notifications_enabled = enabled

    def stop(self):
        self.stopped.set()


def controller(tmp_path):
    module = desktop_module()
    service = Service(tmp_path)
    tray = Tray()
    server = SimpleNamespace(lock=threading.Lock(), url="http://127.0.0.1:4000/?t=secret")
    opened = []
    product = module.DesktopController(service, server, tray, open_url=opened.append)
    return product, service, tray, server, opened


def test_disabled_watcher_is_not_ticked_by_desktop_polling(tmp_path):
    product, service, tray, _, _ = controller(tmp_path)
    product.check()
    product.check()
    assert service.reset_watcher.ticks == 0
    assert service.refreshes == 0
    assert "Primary" in tray.menus[-1][0]


def test_enabled_watcher_checks_when_due_without_repeated_remote_polling(tmp_path):
    product, service, _, _, _ = controller(tmp_path)
    service.reset_watcher.enabled = True
    product.check()
    product.check()
    assert service.reset_watcher.ticks == 1
    product.handle("watcher")
    product.check()
    assert not service.reset_watcher.enabled
    assert service.reset_watcher.ticks == 1


def test_first_watcher_tick_notifies_new_event_without_replaying_history(tmp_path):
    product, service, tray, _, _ = controller(tmp_path)
    service.events = [{"id": "old", "kind": "login_required", "from": 1, "to": 2}]
    service.reset_watcher.enabled = True
    service.reset_watcher.on_tick = lambda: service.events.insert(0, {"id": "new", "kind": "all_exhausted", "from": 1, "to": None})
    product.check()
    assert len(tray.messages) == 1
    assert "limit" in tray.messages[0][0]


def test_initial_cached_snapshot_login_problem_is_not_suppressed(tmp_path):
    product, service, tray, _, _ = controller(tmp_path)
    service.events = [{"id": "old", "kind": "switch_confirmed", "from": 1, "to": 2}]
    service.on_snapshot = lambda: service.events.insert(0, {"id": "new", "kind": "login_required", "from": 2, "to": None}) if not any(event["id"] == "new" for event in service.events) else None
    product.check()
    assert len(tray.messages) == 1
    assert "Backup" in tray.messages[0][1]


def test_next_reset_ignores_disabled_and_stale_accounts(tmp_path):
    from datetime import datetime, timezone
    product, service, tray, _, _ = controller(tmp_path)
    future = datetime.fromtimestamp(time.time() + 3600, timezone.utc).isoformat()
    service.rows[0]["disabled"] = True
    service.rows[1]["usageAgeSeconds"] = 700
    for row in service.rows:
        row["usage"]["fiveHour"]["resetsAt"] = future
    product.check()
    assert "Reset Unknown" in tray.menus[-1][0]


def test_manual_tray_switch_uses_the_same_lock_as_http_actions(tmp_path):
    product, service, _, server, _ = controller(tmp_path)
    service.inside_switch = threading.Event()
    service.finish_switch = threading.Event()
    worker = threading.Thread(target=product.handle, args=("switch:2",))
    worker.start()
    assert service.inside_switch.wait(2)
    assert not server.lock.acquire(blocking=False)
    service.finish_switch.set()
    worker.join(2)
    assert not worker.is_alive() and service.active_number == 2
    assert server.lock.acquire(blocking=False)
    server.lock.release()


def test_dashboard_menu_opens_in_memory_url_without_persisting_its_token(tmp_path):
    product, _, _, _, opened = controller(tmp_path)
    product.handle("dashboard")
    assert opened == ["http://127.0.0.1:4000/?t=secret"]
    product.write_heartbeat()
    state = json.loads((tmp_path / "sessioner-desktop.json").read_text())
    assert set(state) == {"pid", "heartbeatAt", "watcherRunning"}
    assert "secret" not in json.dumps(state)


def test_runtime_status_requires_fresh_heartbeat_and_a_live_process(tmp_path):
    module = desktop_module()
    path = tmp_path / "sessioner-desktop.json"
    path.write_text(json.dumps({"pid": os.getpid(), "heartbeatAt": time.time(), "watcherRunning": True}))
    status = module.status_for(tmp_path)
    assert status["running"] is True and status["watcherRunning"] is True
    assert status["pid"] == os.getpid()
    path.write_text(json.dumps({"pid": os.getpid(), "heartbeatAt": time.time() - 60, "watcherRunning": True}))
    assert module.status_for(tmp_path)["running"] is False
    path.write_text(json.dumps({"pid": 2_147_483_647, "heartbeatAt": time.time(), "watcherRunning": True}))
    assert module.status_for(tmp_path)["watcherRunning"] is False


def test_duplicate_launch_activates_existing_window_without_new_server(tmp_path, monkeypatch):
    module = desktop_module()
    operations = []

    class Existing:
        already_running = True
        def __init__(self, state_dir):
            assert Path(state_dir) == tmp_path
        def activate(self, *, open_browser=True):
            operations.append(("activate", open_browser))
            return True
        def close(self):
            operations.append(("close",))

    monkeypatch.setattr(module, "DesktopInstance", Existing)
    monkeypatch.setattr(module, "SessionerServer", lambda *_: pytest.fail("A duplicate desktop created another web server"))
    assert module.run_desktop(Service(tmp_path)) == 0
    assert operations == [("activate", True), ("close",)]


def test_controller_shutdown_closes_threads_server_and_runtime(tmp_path):
    module = desktop_module()
    from sessioner.web.server import SessionerServer
    service = Service(tmp_path)
    server = SessionerServer(service)
    tray = Tray()
    product = module.DesktopController(service, server, tray, open_url=lambda _: None)
    product.start(open_browser=False)
    deadline = time.monotonic() + 2
    while not (tmp_path / "sessioner-desktop.json").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert module.status_for(tmp_path)["running"] is True
    product.stop()
    assert tray.stopped.is_set()
    assert not product.worker.is_alive() and not product.http_thread.is_alive()
    assert not (tmp_path / "sessioner-desktop.json").exists()


def test_desktop_cli_dispatches_without_open_when_requested(tmp_path, monkeypatch):
    module = desktop_module()
    from sessioner.cli import main
    result = []
    monkeypatch.setattr(module, "run_desktop", lambda service, *, open_browser: result.append(open_browser) or 0)
    assert main(["desktop", "--no-open"], service=Service(tmp_path)) == 0
    assert result == [False]


def test_real_cached_login_problem_notifies_with_watcher_off(tmp_path):
    from test_cli import Engine, row, service
    from sessioner.web.server import SessionerServer

    engine = Engine([row(1, "primary@example.test", "Primary"), row(2, "backup@example.test", "Backup")], login="primary@example.test")
    engine.backup_dir = tmp_path / "store"
    engine.rows[1]["usageStatus"] = "token_expired"
    svc = service(tmp_path, engine)
    server = SessionerServer(svc)
    tray = Tray()
    product = desktop_module().DesktopController(svc, server, tray, open_url=lambda _: None)
    try:
        product.check()
        product.check()
        assert svc.watcher().status()["enabled"] is False
        assert [(item["kind"], item["from"]) for item in svc.activity()["items"]] == [("login_required", 2)]
        assert len(tray.messages) == 1 and "Backup" in tray.messages[0][1]
        assert not [call for call in engine.calls if call == ("list", None)]
    finally:
        server.server_close()


def test_quit_during_real_quota_refresh_cancels_switch_without_disabling_preference(tmp_path):
    from test_cli import Engine, row, service
    from sessioner.web.server import SessionerServer

    entered, release = threading.Event(), threading.Event()

    class SlowEngine(Engine):
        def list_accounts(self, *, json_output, fetch=None):
            if fetch is None:
                entered.set()
                assert release.wait(5)
            return super().list_accounts(json_output=json_output, fetch=fetch)

    engine = SlowEngine([
        row(1, "primary@example.test", "Primary", usage={"fiveHour": {"pct": 100}}),
        row(2, "backup@example.test", "Backup", usage={"fiveHour": {"pct": 10}}),
    ], login="primary@example.test")
    engine.backup_dir = tmp_path / "store"
    svc = service(tmp_path, engine)
    svc.watcher().set_enabled(True)
    server = SessionerServer(svc)
    tray = Tray()
    product = desktop_module().DesktopController(svc, server, tray, open_url=lambda _: None)
    product.start(open_browser=False)
    try:
        assert entered.wait(3)
        product.request_stop()
        release.set()
        product.stop()
        assert engine.login == "primary@example.test"
        assert not [call for call in engine.calls if call[0] == "switch"]
        assert svc.watcher().status()["enabled"] is True
        assert not desktop_module().status_for(svc.state_dir)["running"]
    finally:
        release.set()
        product.stop()


def test_the_tray_menu_can_open_and_quit_before_and_after_a_failed_status_read(tmp_path):
    product, service, tray, _, _ = controller(tmp_path)
    product.render_fallback()
    actions = [item.action for item in tray.menus[-1][1]]
    assert "dashboard" in actions and "quit" in actions

    def broken(**_):
        product._stop.set()  # let the worker make exactly one pass
        raise RuntimeError("engine unavailable")

    service.snapshot = broken
    product._actions.put_nowait("wake")
    product._work()  # one pass: the failed check leaves a usable menu behind
    assert product._error and "quit" in [item.action for item in tray.menus[-1][1]]


def test_a_double_click_opens_the_dashboard_once(tmp_path):
    product, _, _, _, opened = controller(tmp_path)
    moments = iter([100.0, 100.4, 102.0])
    product.clock = lambda: next(moments)
    for _ in range(3):
        product.handle("dashboard")
    assert len(opened) == 2


def test_with_the_watcher_off_an_exhausted_account_is_rechecked_now_and_then(tmp_path):
    product, service, _, _, _ = controller(tmp_path)
    service.exhausted_slots = lambda: [1]
    now = [1000.0]
    product.clock = lambda: now[0]
    product.check()
    product.check()
    assert service.refreshes == 1
    now[0] += 301
    product.check()
    assert service.refreshes == 2
    service.exhausted_slots = lambda: []
    now[0] += 301
    product.check()
    assert service.refreshes == 2
