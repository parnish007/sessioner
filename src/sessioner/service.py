"""Product policy around the inherited account engine and user hook settings."""

from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import dataclass
import io
import json
import math
from pathlib import Path
import shutil
from typing import Callable

from claude_swap import paths, quota_hook
from claude_swap.models import normalize_alias


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
    if account.get("disabled") or account.get("usageStatus") != "ok" or not _finite(age) or not 0 <= age <= 300:
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
        identity = _account_identity(self.active)
        return any(_account_identity(account) != identity and usage_available(account) for account in self.accounts)

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
            from claude_swap.switcher import ClaudeAccountSwitcher
            switcher = ClaudeAccountSwitcher()
        self.switcher = switcher
        self.settings_path = Path(settings_path) if settings_path is not None else paths.get_claude_config_home() / "settings.json"
        self.which = which

    def _call(self, operation: str, action: Callable, next_step: str = "sessioner doctor"):
        try:
            return action()
        except SessionerError:
            raise
        except Exception as exc:
            from claude_swap.exceptions import CredentialError, LockError
            detail = str(exc).lower()
            if "no active claude account" in detail or "no credentials" in detail:
                raise SessionerError("No usable Claude login was found.", "Use /login in Claude Code, then run sessioner add") from exc
            if "does not belong" in detail or "belongs to organization" in detail or "changed during" in detail:
                raise SessionerError("Claude login details disagree or changed while saving. Recheck the login.", "Use /login in Claude Code, then run sessioner add") from exc
            if isinstance(exc, CredentialError):
                raise SessionerError(f"Could not {operation}: the account credentials could not be read or written.", "Check access to your Claude login, then run sessioner doctor") from exc
            if isinstance(exc, LockError):
                raise SessionerError(f"Could not {operation}: another account operation is in progress.", "Wait for it to finish, then retry the command") from exc
            raise SessionerError(f"Could not {operation}.", next_step) from exc

    def snapshot(self, *, refresh: bool = False) -> Snapshot:
        roster = self._call("read saved accounts", lambda: self.switcher.list_accounts(json_output=True, fetch=None if refresh else set()))
        if not isinstance(roster, dict) or roster.get("error") or not isinstance(roster.get("accounts"), list):
            raise SessionerError("Could not read saved accounts. Check the account store with sessioner doctor.")
        accounts = roster["accounts"]
        if any(not isinstance(account, dict) or not isinstance(account.get("number"), int) or not isinstance(account.get("email"), str) for account in accounts):
            raise SessionerError("The saved account list needs repair. Run sessioner doctor.")
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
        return Registration(after.active, len(after.accounts) > len(before.accounts), "could not verify that the stored credential" in captured.getvalue())

    def switch(self, identifier: str) -> tuple[dict, bool]:
        identifier = identifier.strip()
        if not identifier:
            raise SessionerError("Choose a saved account name or number.", "sessioner switch <name-or-number>")
        state = self.snapshot()
        target = next((account for account in state.accounts if str(account["number"]) == identifier or str(account.get("alias", "")).casefold() == identifier.casefold()), None)
        if target is None:
            raise SessionerError(f"No saved account matches '{identifier}'.", "sessioner accounts, then sessioner switch <name-or-number>")
        result = self._call("switch accounts", lambda: self.switcher.switch_to(str(target["number"]), json_output=True))
        if not isinstance(result, dict) or result.get("error") or not isinstance(result.get("to"), dict) or result["to"].get("number") != target["number"]:
            raise SessionerError("The account switch could not be confirmed.", "sessioner status")
        after = self.snapshot()
        if not after.active or after.active["number"] != target["number"]:
            raise SessionerError("The selected login is not active yet.", "sessioner doctor")
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
