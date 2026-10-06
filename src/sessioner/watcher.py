"""Optional local reset watcher.

The watcher is deliberately separate from the Claude hook.  It can refresh
quota and perform a verified account switch when explicitly enabled, but it
never submits or retries a Claude request.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import tempfile
import time
from typing import Callable

from sessioner.accounts.selection import account_eligibility, select_backup
from sessioner.service import SessionerError


DEFAULT_INTERVAL_SECONDS = 60.0
UNKNOWN_RESET_INTERVAL_SECONDS = 300.0


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
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f".{self.state_path.name}.", suffix=".tmp", dir=self.state_path.parent)
        temporary = Path(name)
        try:
            with open(fd, "w", encoding="utf-8", closefd=True) as stream:
                json.dump(state, stream, ensure_ascii=False, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
            temporary.replace(self.state_path)
        finally:
            temporary.unlink(missing_ok=True)

    def status(self) -> dict:
        state = self._read()
        return dict(state)

    def set_enabled(self, enabled: bool) -> dict:
        if not isinstance(enabled, bool):
            raise SessionerError("Choose on or off.")
        state = self._read()
        state["enabled"] = enabled
        state["state"] = "armed" if enabled else "off"
        state["reason"] = "enabled" if enabled else None
        state["nextPollAt"] = None
        self._write(state)
        return dict(state)

    def _save_tick(self, state: dict, *, now: float) -> dict:
        state["lastPollAt"] = now
        self._write(state)
        return dict(state)

    def tick(self, *, interval: float = DEFAULT_INTERVAL_SECONDS) -> dict:
        if not _number(interval) or interval <= 0:
            raise SessionerError("The watcher interval must be greater than zero.", "sessioner watch --interval 60")
        state = self._read()
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
            snapshot = self.service.snapshot(refresh=True)
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
                # Work down the ranked candidates: one account that cannot be switched to
                # must not stop the watcher using another that has room.
                failure = "switch-failed"
                for candidate in result.ranked or (result.target,):
                    try:
                        account, changed = self.service.switch(str(candidate))
                    except Exception:
                        failure = "switch-failed"
                        continue
                    if not changed or account.get("number") != candidate:
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
                next_poll = result.earliest_reset_at.timestamp()
                if next_poll <= now:
                    next_poll = now + interval
                state.update({"state": "waiting", "reason": result.reason, "nextPollAt": next_poll})
                return self._save_tick(state, now=now)
            if result.reset_unknown:
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
        while True:
            state = self.tick(interval=interval)
            if on_tick:
                on_tick(dict(state))
            if once or not state.get("enabled"):
                return 0
            now = float(self.clock())
            next_poll = state.get("nextPollAt")
            delay = interval
            if _number(next_poll):
                delay = max(0.0, float(next_poll) - now)
            self.sleeper(delay)

