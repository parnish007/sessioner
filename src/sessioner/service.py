"""Product policy around the inherited account engine and user hook settings."""

from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
import io
import json
import math
from pathlib import Path
import shutil
import threading
import time
from typing import Callable

from sessioner.accounts import history, paths, process_detection, quota_hook
from sessioner.accounts.models import normalize_alias
from sessioner.accounts.selection import USAGE_MAX_AGE_SECONDS, select_backup


LOCK_RETRIES = 3


class SessionerError(Exception):
    def __init__(self, message: str, next_step: str = "sessioner doctor"):
        super().__init__(message)
        self.next_step = next_step


def account_label(account: dict) -> str:
    name = account.get("alias")
    email = account.get("email", "Unknown login")
    return f"{name} ({email})" if name else email


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _account_identity(account: dict) -> tuple[str, str]:
    return (account.get("email", "").strip().casefold(), (account.get("organizationUuid") or "").strip().casefold())


def usage_available(account: dict) -> bool:
    """The hook requires fresh measured quota, including any model limits."""
    age = account.get("usageAgeSeconds")
    usage = account.get("usage")
    if account.get("disabled") or account.get("usageStatus") != "ok" or not _finite(age) or not 0 <= age <= USAGE_MAX_AGE_SECONDS:
        return False
    if not isinstance(usage, dict):
        return False
    windows = [usage[key] for key in ("fiveHour", "sevenDay") if key in usage]
    scoped = usage.get("scoped", [])
    if not windows or not isinstance(scoped, list):
        return False
    return all(isinstance(window, dict) and _finite(window.get("pct")) and 0 <= window["pct"] < 100 for window in windows + scoped)


@dataclass
class Snapshot:
    accounts: list[dict]
    active: dict | None
    login_email: str | None
    hook_installed: bool
    hooks_disabled: bool
    settings_problem: str | None
    claude_path: str | None

    @property
    def enabled(self) -> list[dict]:
        seen = set()
        accounts = []
        for account in self.accounts:
            identity = _account_identity(account)
            if not account.get("disabled") and identity not in seen:
                seen.add(identity)
                accounts.append(account)
        return accounts

    @property
    def backup_available(self) -> bool:
        if not self.active:
            return False
        return select_backup(
            self.accounts,
            active_number=self.active.get("number"),
            now=datetime.now(timezone.utc),
        ).target is not None

    def enable_problem(self) -> SessionerError | None:
        if self.settings_problem:
            return SessionerError(self.settings_problem, "Fix the Claude settings shown by sessioner doctor, then run sessioner on")
        if self.hooks_disabled:
            return SessionerError("Claude has all hooks disabled. Automatic switching cannot run.", "Set disableAllHooks to false in Claude settings, then run sessioner on")
        if not self.claude_path:
            return SessionerError("Claude Code was not found in this terminal.", "Install Claude Code or make its claude command available, then run sessioner doctor")
        if len(self.enabled) < 2:
            return SessionerError("Save at least two different enabled Claude accounts first.", "sessioner setup")
        if not self.active or self.active.get("disabled"):
            return SessionerError("The active login must be a saved, enabled account.", "sessioner add, or sessioner switch <name-or-number>")
        return None


@dataclass
class Registration:
    account: dict
    created: bool
    ownership_unverified: bool = False


class SessionerService:
    def __init__(self, switcher=None, settings_path: Path | None = None, which: Callable = shutil.which):
        if switcher is None:
            from sessioner.accounts.switcher import ClaudeAccountSwitcher
            switcher = ClaudeAccountSwitcher()
        self.switcher = switcher
        self.settings_path = Path(settings_path) if settings_path is not None else paths.get_claude_config_home() / "settings.json"
        self.which = which
        self.sleep = time.sleep
        self._ledger = None
        self._statistics_lock = threading.RLock()
        self._preferences = None

    def _call(self, operation: str, action: Callable, next_step: str = "sessioner doctor"):
        try:
            return action()
        except SessionerError:
            raise
        except Exception as exc:
            from sessioner.accounts.exceptions import CredentialError, LockError
            detail = str(exc).lower()
            if "no active claude account" in detail or "no credentials" in detail:
                raise SessionerError("No usable Claude login was found.", "Use /login in Claude Code, then run sessioner add") from exc
            if "does not belong" in detail or "belongs to organization" in detail or "changed during" in detail:
                raise SessionerError("Claude login details disagree or changed while saving. Recheck the login.", "Use /login in Claude Code, then run sessioner add") from exc
            if isinstance(exc, CredentialError):
                raise SessionerError(f"Could not {operation}: the account credentials could not be read or written.", "Check access to your Claude login, then run sessioner doctor") from exc
            if isinstance(exc, LockError):
                busy = SessionerError(f"Could not {operation}: another account operation is in progress.", "Wait for it to finish, then retry the command")
                busy.retryable = True
                raise busy from exc
            raise SessionerError(f"Could not {operation}.", next_step) from exc

    def snapshot(self, *, refresh: bool = False, source: str = "manual") -> Snapshot:
        roster = self._call("read saved accounts", lambda: self.switcher.list_accounts(json_output=True, fetch=None if refresh else set()))
        if not isinstance(roster, dict) or roster.get("error") or not isinstance(roster.get("accounts"), list):
            raise SessionerError("Could not read saved accounts. Check the account store with sessioner doctor.")
        accounts = roster["accounts"]
        if any(not isinstance(account, dict) or not isinstance(account.get("number"), int) or not isinstance(account.get("email"), str) for account in accounts):
            raise SessionerError("The saved account list needs repair. Run sessioner doctor.")
        from sessioner.activity import FILE, ActivityStore

        # Known login failures can notify from the cache. Quota recovery is only
        # checked on refresh, with proof that its measurement follows exhaustion.
        ActivityStore(self.state_dir / FILE).observe_accounts(accounts, source=source, include_quota=refresh)
        active_number = roster.get("activeAccountNumber")
        active = next((account for account in accounts if account["number"] == active_number), None)
        email = active["email"] if active else None
        if active is None:
            current = self._call("read the current Claude login", lambda: self.switcher.status(json_output=True))
            current_active = current.get("active") if isinstance(current, dict) else None
            email = current_active.get("email") if isinstance(current_active, dict) else None
        problem = None
        installed = disabled = False
        try:
            installed = quota_hook.hook_enabled(self.settings_path)
            settings = json.loads(self.settings_path.read_text(encoding="utf-8-sig")) if self.settings_path.exists() else {}
            disabled = settings.get("disableAllHooks") is True
            if settings.get("allowManagedHooksOnly") is True:
                problem = f"Claude allows only managed hooks; the Sessioner user hook cannot run. Check {self.settings_path}."
        except (OSError, ValueError, UnicodeError):
            problem = f"Claude settings could not be read as valid hook settings: {self.settings_path}. Repair this file before changing hooks."
        return Snapshot(accounts, active, email, installed, disabled, problem, self.which("claude"))

    def live_sessions(self):
        """Return live Claude metadata and the number of unreadable records.

        Sessioner intentionally delegates filtering to Claude's session
        detector; this call reads session metadata only.  The settings directory is
        also the profile directory whose account is shared by every session.
        """
        with self._statistics_lock:
            if not self.preferences().read()["statisticsEnabled"]:
                return [], 0
            return process_detection.scan_sessions(claude_dir=self.settings_path.parent)

    @property
    def state_dir(self) -> Path:
        """Where Sessioner keeps its own non-secret state, beside the saved accounts."""
        return Path(getattr(self.switcher, "backup_dir", None) or paths.get_backup_root())

    def watcher(self):
        """The optional reset watcher; its state file holds no credentials."""
        from sessioner.watcher import ResetWatcher  # imported here: the watcher imports this module

        return ResetWatcher(self, state_path=self.state_dir / "sessioner-watcher.json")

    def preferences(self):
        from sessioner.preferences import FILE, PreferenceStore

        if self._preferences is None:
            self._preferences = PreferenceStore(self.state_dir / FILE, on_change=self._preferences_changed)
        return self._preferences

    def _preferences_changed(self, value: dict) -> None:
        with self._statistics_lock:
            if value.get("statisticsEnabled") is False:
                self._ledger = None

    def set_preferences(self, **changes: bool) -> dict:
        """Privacy changes wait for ongoing reads before completing."""
        with self._statistics_lock:
            return self.preferences().update(**changes)

    def activity(self, limit: int = 50) -> dict:
        from sessioner.activity import FILE, ActivityStore

        return ActivityStore(self.state_dir / FILE).read(limit=limit)

    def hook_last_call(self) -> dict:
        """When Claude last called the limit hook, and how that call ended."""
        return quota_hook.last_call(self.state_dir)

    def exhausted_slots(self) -> list[int]:
        from sessioner.activity import FILE, ActivityStore

        return ActivityStore(self.state_dir / FILE).exhausted_slots()

    def record_event(self, kind: str, source: str = "manual", from_slot=None, to_slot=None, reason=None):
        from sessioner.activity import FILE, ActivityStore

        return ActivityStore(self.state_dir / FILE).record(kind, source=source, from_slot=from_slot, to_slot=to_slot, reason=reason)

    def note_active(self, account: dict | None) -> None:
        """Remember which login is active, so usage can be attributed to the right account later."""
        history.note_active(self.state_dir, account)

    def token_report(self, accounts: list[dict], live_ids) -> dict:
        """Token usage per session and account, read from the counters Claude records locally."""
        with self._statistics_lock:
            if not self.preferences().read()["statisticsEnabled"]:
                self._ledger = None
                return {
                    "available": False, "reason": "statistics-disabled",
                    "totals": {"input": 0, "output": 0, "cacheWrite": 0, "cacheRead": 0, "total": 0, "messages": 0},
                    "trackedSince": None, "accounts": [], "sessions": [], "sessionCount": 0,
                }
            from sessioner.tokens import TokenLedger

            if self._ledger is None:
                self._ledger = TokenLedger(self.settings_path.parent / "projects")
            return self._ledger.report(history=history.read(self.state_dir / history.FILE), accounts=accounts, live_ids=live_ids)

    def rename(self, identifier: str, name: str) -> dict:
        identifier = identifier.strip()
        state = self.snapshot()
        target = next((account for account in state.accounts if str(account["number"]) == identifier or str(account.get("alias", "")).casefold() == identifier.casefold()), None)
        if target is None:
            raise SessionerError(f"No saved account matches '{identifier}'.", "sessioner accounts, then sessioner rename <name-or-number> <new-name>")
        try:
            proposed = normalize_alias(name)
        except ValueError as exc:
            raise SessionerError("Use a nonnumeric account name with letters, digits, dots, dashes, or underscores; start without a dash.", "sessioner rename <name-or-number> <new-name>") from exc
        if any(str(account.get("alias", "")).casefold() == proposed and account["number"] != target["number"] for account in state.accounts):
            raise SessionerError(f"The account name '{proposed}' is already in use.", "sessioner rename <name-or-number> <different-name>")
        self._call("rename the account", lambda: self.switcher.set_alias(str(target["number"]), proposed))
        renamed = next((account for account in self.snapshot().accounts if account["number"] == target["number"]), None)
        if renamed is None or renamed.get("alias") != proposed:
            raise SessionerError("The new account name could not be confirmed.", "sessioner accounts")
        return renamed

    def default_name(self, state: Snapshot | None = None) -> str:
        state = state or self.snapshot()
        if state.active and state.active.get("alias"):
            return state.active["alias"]
        names = {str(account.get("alias", "")).casefold() for account in state.accounts}
        for name in ("primary", "backup"):
            if name not in names:
                return name
        number = max((account["number"] for account in state.accounts), default=0) + 1
        while f"account-{number}" in names:
            number += 1
        return f"account-{number}"

    def add(self, name: str | None = None) -> Registration:
        before = self.snapshot()
        if not before.login_email:
            raise SessionerError("No active Claude login was found.", "Use /login in Claude Code, then run sessioner add")
        try:
            proposed = normalize_alias(name if name is not None else self.default_name(before))
        except ValueError as exc:
            raise SessionerError("Use a nonnumeric account name with letters, digits, dots, dashes, or underscores; start without a dash.", "sessioner add primary") from exc
        current_number = before.active.get("number") if before.active else None
        if any(str(account.get("alias", "")).casefold() == proposed and account["number"] != current_number for account in before.accounts):
            raise SessionerError(f"The account name '{proposed}' is already in use.", "sessioner add <different-name>")
        captured = io.StringIO()
        with redirect_stdout(captured):
            self._call("save the current account", lambda: self.switcher.add_account(slot=None, alias=proposed))
        after = self.snapshot()
        if not after.active or after.active.get("alias") != proposed:
            raise SessionerError("The saved account could not be confirmed.", "sessioner accounts")
        self.note_active(after.active)
        return Registration(after.active, len(after.accounts) > len(before.accounts), "could not verify that the stored credential" in captured.getvalue())

    def switch(self, identifier: str, *, source: str = "manual", reason: str | None = None) -> tuple[dict, bool]:
        identifier = identifier.strip()
        if not identifier:
            raise SessionerError("Choose a saved account name or number.", "sessioner switch <name-or-number>")
        state = self.snapshot()
        target = next((account for account in state.accounts if str(account["number"]) == identifier or str(account.get("alias", "")).casefold() == identifier.casefold()), None)
        if target is None:
            raise SessionerError(f"No saved account matches '{identifier}'.", "sessioner accounts, then sessioner switch <name-or-number>")
        from_slot = state.active.get("number") if state.active else None
        changed_target = from_slot != target["number"]
        if changed_target:
            self.record_event("candidate_selected", source, from_slot, target["number"], reason or ("manual-selection" if source == "manual" else None))
        # The hook or another window can hold the account lock for a moment. Wait it out a
        # few times before telling the person it failed; never spin.
        for attempt in range(LOCK_RETRIES + 1):
            try:
                result = self._call("switch accounts", lambda: self.switcher.switch_to(str(target["number"]), json_output=True))
                break
            except SessionerError as exc:
                if not getattr(exc, "retryable", False) or attempt == LOCK_RETRIES:
                    self.record_event("switch_failed", source, from_slot, target["number"], "account-busy" if getattr(exc, "retryable", False) else "switch-failed")
                    raise
                self.sleep(0.4 * (attempt + 1))
        if not isinstance(result, dict) or result.get("error") or not isinstance(result.get("to"), dict) or result["to"].get("number") != target["number"]:
            self.record_event("switch_failed", source, from_slot, target["number"], "switch-unverified")
            raise SessionerError("The account switch could not be confirmed.", "sessioner status")
        try:
            after = self.snapshot()
        except SessionerError:
            self.record_event("switch_failed", source, from_slot, target["number"], "switch-unverified")
            raise
        if not after.active or after.active["number"] != target["number"]:
            self.record_event("switch_failed", source, from_slot, target["number"], "switch-unverified")
            raise SessionerError("The selected login is not active yet.", "sessioner doctor")
        self.note_active(after.active)
        if result.get("switched") is True:
            self.record_event("switch_confirmed", source, from_slot, target["number"], "verified")
        return after.active, result.get("switched") is True

    def set_automatic(self, enabled: bool) -> dict:
        if enabled:
            problem = self.snapshot().enable_problem()
            if problem:
                raise problem
        try:
            result = quota_hook.configure_hook(self.settings_path, enabled)
        except (OSError, ValueError, UnicodeError) as exc:
            raise SessionerError(f"Could not change automatic switching. Check Claude settings: {self.settings_path}.", "Repair the settings shown by sessioner doctor, then retry") from exc
        if quota_hook.hook_enabled(self.settings_path) is not enabled:
            raise SessionerError("The automatic switching setting could not be confirmed.")
        return result
