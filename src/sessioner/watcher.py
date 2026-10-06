"""Optional local reset watcher.

The watcher is deliberately separate from the Claude hook.  It can refresh
quota and perform a verified account switch when explicitly enabled, but it
never submits or retries a Claude request.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import threading
import time
from typing import Callable
from uuid import uuid4

from sessioner.accounts.process_detection import is_pid_alive
from sessioner.accounts.exceptions import LockError
from sessioner.accounts.locking import FileLock
from sessioner.accounts.selection import account_eligibility, select_backup
from sessioner.preferences import atomic_json, read_json
from sessioner.service import SessionerError


DEFAULT_INTERVAL_SECONDS = 60.0
UNKNOWN_RESET_INTERVAL_SECONDS = 300.0
HEARTBEAT_INTERVAL_SECONDS = 5.0
HEARTBEAT_MAX_AGE_SECONDS = 20.0
RUNTIME_FILE = "sessioner-watcher-runtime.json"


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class ResetWatcher:
    def __init__(
        self,
        service,
        *,
        state_path: Path,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.service = service
        self.state_path = Path(state_path)
        self.clock = clock
        self.sleeper = sleeper

    def _default(self) -> dict:
        return {
            "enabled": False,
            "state": "off",
            "reason": None,
            "lastPollAt": None,
            "nextPollAt": None,
            "lastSwitchFrom": None,
            "lastSwitchTo": None,
        }

    def _read(self) -> dict:
        if not self.state_path.exists():
            return self._default()
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return self._default()
        if not isinstance(value, dict):
            return self._default()
        state = self._default()
        for key in state:
            if key in value:
                state[key] = value[key]
        if not isinstance(state["enabled"], bool):
            return self._default()
        return state

    def _write(self, state: dict) -> None:
        atomic_json(self.state_path, state)

    def _setting_lock(self):
        return FileLock(self.state_path.with_name(f".{self.state_path.name}.lock"), timeout=5)

    def status(self) -> dict:
        state = self._read()
        runtime = read_json(self.state_path.with_name(RUNTIME_FILE))
        pid, heartbeat = runtime.get("pid"), runtime.get("heartbeatAt")
        pid = pid if isinstance(pid, int) and not isinstance(pid, bool) and pid > 1 else None
        heartbeat = heartbeat if _number(heartbeat) else None
        now = self.clock()
        running = bool(
            state.get("enabled") is True and pid is not None and heartbeat is not None and _number(now)
            and 0 <= now - heartbeat <= HEARTBEAT_MAX_AGE_SECONDS and is_pid_alive(pid)
        )
        return {**state, "running": running, "pid": pid, "heartbeatAt": heartbeat}

    def set_enabled(self, enabled: bool) -> dict:
        if not isinstance(enabled, bool):
            raise SessionerError("Choose on or off.")
        with self._setting_lock():
            state = self._read()
            state["enabled"] = enabled
            state["state"] = "armed" if enabled else "off"
            state["reason"] = "enabled" if enabled else None
            state["nextPollAt"] = None
            self._write(state)
        return dict(state)

    def _save_tick(self, state: dict, *, now: float) -> dict:
        with self._setting_lock():
            latest = self._read()
            # A dashboard or another CLI can change the setting while quota is loading.
            # A completed poll must never restore its stale enabled flag.
            if not latest["enabled"]:
                state.update({"enabled": False, "state": "off", "reason": None, "nextPollAt": None})
            elif not state["enabled"]:
                state = latest
            state["lastPollAt"] = now
            self._write(state)
        return dict(state)

    def tick(self, *, interval: float = DEFAULT_INTERVAL_SECONDS, cancelled: Callable[[], bool] | None = None) -> dict:
        if not _number(interval) or interval <= 0:
            raise SessionerError("The watcher interval must be greater than zero.", "sessioner watch --interval 60")
        try:
            # Desktop and CLI workers share this profile. A contender leaves the
            # running poll alone instead of selecting from the same old login.
            with FileLock(self.state_path.with_name(".sessioner-watcher-poll.lock"), timeout=0):
                return self._tick(interval=interval, cancelled=cancelled)
        except LockError:
            return self._read()

    def _tick(self, *, interval: float, cancelled: Callable[[], bool] | None) -> dict:
        state = self._read()
        if cancelled and cancelled():
            return dict(state)
        now = float(self.clock())
        if not _number(now):
            now = time.time()
        if not state["enabled"]:
            state["state"] = "off"
            state["reason"] = None
            state["nextPollAt"] = None
            return self._save_tick(state, now=now)
        if state.get("state") in {"waiting", "reset_unknown"} and _number(state.get("nextPollAt")) and now < state["nextPollAt"]:
            return dict(state)

        state["lastPollAt"] = now
        try:
            snapshot = self.service.snapshot(refresh=True, source="watcher")
            if cancelled and cancelled():
                return dict(state)
            if not self._read()["enabled"]:
                return self._save_tick(state, now=now)
            active = snapshot.active
            if not active:
                state.update({"state": "blocked", "reason": "no-active-account", "nextPollAt": now + interval})
                return self._save_tick(state, now=now)
            eligibility = account_eligibility(
                active,
                now=datetime.fromtimestamp(now, tz=timezone.utc),
            )
            if eligibility.reason == "available":
                state.update({"state": "armed", "reason": "active-has-headroom", "nextPollAt": now + interval})
                return self._save_tick(state, now=now)
            if eligibility.reason != "exhausted":
                state.update({"state": "blocked", "reason": eligibility.reason, "nextPollAt": now + interval})
                return self._save_tick(state, now=now)

            result = select_backup(
                snapshot.accounts,
                active_number=active.get("number"),
                now=datetime.fromtimestamp(now, tz=timezone.utc),
            )
            if result.target is not None:
                self.service.record_event("quota_exhausted", source="watcher", from_slot=active.get("number"), reason="quota-exhausted")
                # Work down the ranked candidates: one account that cannot be switched to
                # must not stop the watcher using another that has room.
                failure = "switch-failed"
                for candidate in result.ranked or (result.target,):
                    if cancelled and cancelled():
                        return dict(state)
                    if not self._read()["enabled"]:
                        return self._save_tick(state, now=now)
                    try:
                        account, changed = self.service.switch(str(candidate), source="watcher", reason=result.reason)
                    except Exception:
                        failure = "switch-failed"
                        continue
                    if not changed and account.get("number") == candidate:
                        # A manual switch or another controller already picked
                        # this login. It is confirmed active; do not try another.
                        state.update({"state": "armed", "reason": "active-has-headroom", "nextPollAt": now + interval})
                        return self._save_tick(state, now=now)
                    if account.get("number") != candidate:
                        self.service.record_event("switch_failed", source="watcher", from_slot=active.get("number"), to_slot=candidate, reason="switch-unverified")
                        failure = "switch-unverified"
                        continue
                    state.update({
                        "state": "switched",
                        "reason": result.reason,
                        "nextPollAt": now + interval,
                        "lastSwitchFrom": active.get("number"),
                        "lastSwitchTo": candidate,
                    })
                    return self._save_tick(state, now=now)
                state.update({"state": "blocked", "reason": failure, "nextPollAt": now + interval})
                return self._save_tick(state, now=now)
            if result.all_exhausted and result.earliest_reset_at is not None:
                if state.get("reason") != result.reason:
                    self.service.record_event("all_exhausted", source="watcher", from_slot=active.get("number"), reason=result.reason)
                next_poll = result.earliest_reset_at.timestamp()
                if next_poll <= now:
                    next_poll = now + interval
                state.update({"state": "waiting", "reason": result.reason, "nextPollAt": next_poll})
                return self._save_tick(state, now=now)
            if result.reset_unknown:
                if state.get("reason") != result.reason:
                    self.service.record_event("all_exhausted", source="watcher", from_slot=active.get("number"), reason=result.reason)
                state.update({"state": "reset_unknown", "reason": result.reason, "nextPollAt": now + max(interval, UNKNOWN_RESET_INTERVAL_SECONDS)})
                return self._save_tick(state, now=now)
            state.update({"state": "blocked", "reason": result.reason, "nextPollAt": now + interval})
            return self._save_tick(state, now=now)
        except Exception:
            state.update({"state": "blocked", "reason": "refresh-failed", "nextPollAt": now + interval})
            return self._save_tick(state, now=now)

    def run(
        self,
        *,
        once: bool = False,
        interval: float = DEFAULT_INTERVAL_SECONDS,
        on_tick: Callable[[dict], None] | None = None,
    ) -> int:
        if not _number(interval) or interval <= 0:
            raise SessionerError("The watcher interval must be greater than zero.", "sessioner watch --interval 60")
        runtime_path = self.state_path.with_name(RUNTIME_FILE)
        run_id = uuid4().hex
        runtime_lock = threading.Lock()
        stopped = threading.Event()

        def heartbeat():
            with runtime_lock:
                if stopped.is_set():
                    return
                now = self.clock()
                atomic_json(runtime_path, {"pid": os.getpid(), "heartbeatAt": now if _number(now) else time.time(), "runId": run_id})

        def keep_alive():
            # Quota fetches can outlast a poll interval. Liveness remains accurate
            # while the account engine is waiting for a response, too.
            while not stopped.wait(HEARTBEAT_INTERVAL_SECONDS):
                try:
                    heartbeat()
                except (OSError, ValueError):
                    pass

        timer = threading.Thread(target=keep_alive, name="sessioner-watcher-heartbeat", daemon=True)

        try:
            heartbeat()
            timer.start()
            while True:
                heartbeat()
                state = self.tick(interval=interval)
                heartbeat()
                if on_tick:
                    runtime = self.status()
                    on_tick({**state, **{key: runtime[key] for key in ("running", "pid", "heartbeatAt")}})
                if once or not state.get("enabled"):
                    return 0
                now = float(self.clock())
                next_poll = state.get("nextPollAt")
                delay = max(0.0, float(next_poll) - now) if _number(next_poll) else interval
                while delay > 0 and self._read()["enabled"]:
                    heartbeat()
                    chunk = min(HEARTBEAT_INTERVAL_SECONDS, delay)
                    self.sleeper(chunk)
                    delay -= chunk
                    heartbeat()
        finally:
            stopped.set()
            if timer.ident is not None:
                timer.join(timeout=5)
            with runtime_lock:
                if read_json(runtime_path).get("runId") == run_id:
                    runtime_path.unlink(missing_ok=True)

