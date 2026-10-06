"""One desktop controller owns the loopback dashboard and optional watcher."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
import webbrowser

from sessioner.accounts.process_detection import is_pid_alive
from sessioner.accounts.selection import account_eligibility
from sessioner.notifications import NotificationEngine, account_name
from sessioner.service import SessionerError
from sessioner.web.server import SessionerServer
from sessioner.wintray import DesktopInstance, MenuItem, NativeTray, wake_for


RUNTIME_FILE = "sessioner-desktop.json"
HEARTBEAT_SECONDS = 5
HEARTBEAT_MAX_AGE = 20


def supported() -> bool:
    return sys.platform == "win32"


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def status_for(state_dir: Path) -> dict:
    """A enabled flag is not liveness: require a current heartbeat and live PID."""
    result = {"running": False, "watcherRunning": False, "pid": None, "heartbeatAt": None}
    path = Path(state_dir) / RUNTIME_FILE
    try:
        if path.stat().st_size > 4096:
            return result
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return result
        pid, heartbeat = raw.get("pid"), raw.get("heartbeatAt")
        if (not isinstance(pid, int) or isinstance(pid, bool) or not 1 < pid <= 0x7FFFFFFF
                or not _number(heartbeat) or not 0 <= time.time() - heartbeat <= HEARTBEAT_MAX_AGE
                or not is_pid_alive(pid)):
            return result
        result.update({"running": True, "watcherRunning": raw.get("watcherRunning") is True,
                       "pid": pid, "heartbeatAt": float(heartbeat)})
    except (OSError, ValueError, UnicodeError, OverflowError):
        pass
    return result


class DesktopController:
    def __init__(self, service, server, tray, *, open_url=webbrowser.open):
        self.service, self.server, self.tray = service, server, tray
        self.open_url = open_url
        self._stop = threading.Event()
        self._actions: queue.Queue[str] = queue.Queue(maxsize=50)
        self._notices = NotificationEngine()
        self._notifications_ready = False
        self._watcher_running = False
        self._error = False
        self._started = False
        self.worker = threading.Thread(target=self._work, name="Sessioner desktop worker", daemon=True)
        self.http_thread = threading.Thread(target=self._serve, name="Sessioner dashboard", daemon=True)
        self.heartbeat_thread = threading.Thread(target=self._heartbeat, name="Sessioner heartbeat", daemon=True)

    def submit(self, action: str) -> None:
        if action == "quit":
            self.request_stop()
        else:
            try:
                self._actions.put_nowait(action)
            except queue.Full:
                pass

    def handle(self, action: str) -> None:
        if action == "dashboard":
            self.open_url(self.server.url)
            return
        if action == "wake":
            return
        if action == "quit":
            self.request_stop()
            return
        with self.server.lock:
            if action == "refresh":
                self.service.snapshot(refresh=True, source="desktop")
            elif action == "watcher":
                watcher = self.service.watcher()
                watcher.set_enabled(not watcher.status().get("enabled"))
            elif action.startswith("switch:") and action[7:].isdigit():
                self.service.switch(action[7:], source="desktop")

    def check(self) -> None:
        # All engine reads, watcher ticks and tray mutations use the HTTP lock.
        with self.server.lock:
            if self._stop.is_set():
                return
            preferences = self.service.preferences().read()
            self.tray.set_notifications_enabled(preferences.get("notificationsEnabled") is True)
            if not self._notifications_ready:
                self._notices.poll(self.service.activity(limit=50).get("items", []), [],
                                   enabled=preferences.get("notificationsEnabled") is True)
                self._notifications_ready = True
            watcher = self.service.watcher()
            watching = watcher.status()
            snapshot = self.service.snapshot(source="desktop")
            self._notify(snapshot.accounts, preferences)
            enabled = watching.get("enabled") is True
            due = watching.get("nextPollAt")
            if enabled and not watching.get("running") and (not _number(due) or time.time() >= due):
                watching = watcher.tick(interval=60, cancelled=self._stop.is_set)
            if self._stop.is_set():
                return
            self._watcher_running = enabled
            snapshot = self.service.snapshot(source="desktop")
            self._notify(snapshot.accounts, preferences)
            self._render(snapshot, watching)

    def _notify(self, accounts: list[dict], preferences: dict) -> None:
        for notice in self._notices.poll(self.service.activity(limit=50).get("items", []), accounts,
                                         enabled=preferences.get("notificationsEnabled") is True):
            self.tray.notify(notice.title, notice.body)

    def _render(self, snapshot, watching: dict) -> None:
        active = snapshot.active
        name = account_name(snapshot.accounts, active.get("number")) if active else "No saved login"
        now = datetime.now(timezone.utc)
        resets = []
        for account in snapshot.accounts:
            eligibility = account_eligibility(account, now=now)
            if eligibility.reason not in {"available", "exhausted"}:
                continue
            if eligibility.next_reset_at:
                resets.append(eligibility.next_reset_at)
        reset_text = min(resets).astimezone().strftime("%a %H:%M") if resets else "Unknown"
        watcher_text = "Running" if watching.get("enabled") else "Off"
        menu = [MenuItem(f"Active: {name}"), MenuItem(f"Next reset: {reset_text}"), MenuItem(f"Reset watcher: {watcher_text}"), MenuItem("")]
        if self._error:
            menu.insert(3, MenuItem("Action needs attention · open dashboard"))
        menu.extend([MenuItem("Open dashboard", "dashboard"), MenuItem("Refresh usage", "refresh"), MenuItem("")])
        for account in snapshot.accounts:
            menu.append(MenuItem(f"Switch to {account_name(snapshot.accounts, account.get('number'))}",
                                 f"switch:{account['number']}", enabled=not account.get("disabled"),
                                 checked=bool(active and account["number"] == active.get("number"))))
        menu.extend([MenuItem(""), MenuItem("Reset watcher", "watcher", checked=watching.get("enabled") is True), MenuItem("Quit Sessioner", "quit")])
        self.tray.update(f"Sessioner · {name} · Reset {reset_text} · Watcher {watcher_text}", menu)

    def write_heartbeat(self) -> None:
        path = Path(self.service.state_dir) / RUNTIME_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        state = {"pid": os.getpid(), "heartbeatAt": time.time(),
                 "watcherRunning": self._watcher_running and self.worker.is_alive() and not self._stop.is_set()}
        fd, filename = tempfile.mkstemp(prefix=".sessioner-desktop.", suffix=".tmp", dir=path.parent)
        temporary = Path(filename)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(state, stream, separators=(",", ":"))
                stream.write("\n")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _heartbeat(self) -> None:
        while not self._stop.is_set():
            try:
                self.write_heartbeat()
            except OSError:
                self._error = True
            self._stop.wait(HEARTBEAT_SECONDS)

    def _work(self) -> None:
        while not self._stop.is_set():
            try:
                self.check()
            except Exception:
                self._error = True
            try:
                action = self._actions.get(timeout=5)
            except queue.Empty:
                continue
            if self._stop.is_set():
                break
            try:
                self.handle(action)
                self._error = False
            except Exception:
                self._error = True

    def _serve(self) -> None:
        try:
            self.server.serve_forever(poll_interval=0.2)
        finally:
            self.request_stop()

    def start(self, *, open_browser: bool = True) -> None:
        self._started = True
        self.server.on_quit = self.request_stop
        self.http_thread.start()
        self.worker.start()
        self.heartbeat_thread.start()
        if open_browser:
            self.open_url(self.server.url)

    def request_stop(self) -> None:
        self._stop.set()
        self.tray.stop()
        try:
            self._actions.put_nowait("wake")
        except queue.Full:
            pass

    def stop(self) -> None:
        self.request_stop()
        if self.http_thread.is_alive():
            self.server.shutdown()
        for thread in (self.worker, self.http_thread, self.heartbeat_thread):
            if thread.is_alive() and thread is not threading.current_thread():
                thread.join()
        self.server.server_close()
        path = Path(self.service.state_dir) / RUNTIME_FILE
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and value.get("pid") == os.getpid():
                path.unlink(missing_ok=True)
        except (OSError, UnicodeError, ValueError):
            pass


def run_desktop(service, *, open_browser: bool = True) -> int:
    if sys.platform != "win32":
        raise SessionerError("The desktop companion requires Windows.", "sessioner ui")
    instance = DesktopInstance(service.state_dir)
    try:
        if instance.already_running:
            if not instance.activate(open_browser=open_browser):
                raise SessionerError("Sessioner is starting. Open the shortcut again in a moment.")
            return 0
        server = SessionerServer(service)
        controller = None
        tray = None
        try:
            tray = NativeTray(service.state_dir, lambda action: controller.submit(action) if controller else None)
            controller = DesktopController(service, server, tray)
            controller.start(open_browser=open_browser)
            tray.run()
        finally:
            if controller:
                controller.stop()
            else:
                server.server_close()
            if tray:
                tray.close()
    finally:
        instance.close()
    return 0


def main(argv=None) -> int:
    """pythonw entrypoint: startup failures appear in a native error dialog."""
    from sessioner.service import SessionerService
    from sessioner.wintray import show_error
    parser = argparse.ArgumentParser(prog="Sessioner desktop")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args(argv)
    try:
        return run_desktop(SessionerService(), open_browser=not args.no_open)
    except SessionerError as exc:
        show_error(f"{exc}\n\nNext: {exc.next_step}")
    except Exception:
        show_error("Sessioner could not start. Run sessioner doctor from the Sessioner folder to check setup.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
