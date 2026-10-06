"""Browser-facing view of Sessioner state and the few actions the page may take.

The page never receives credentials, tokens, or conversation data: only account
names, emails, usage percentages, and the stage of setup.
"""

from __future__ import annotations

from sessioner.service import SessionerError, SessionerService, Snapshot, _account_identity, _finite, account_label, usage_available

API_VERSION = 1

_WINDOWS = (("fiveHour", "5-hour"), ("sevenDay", "Weekly"))
_NOTES = {
    "token_expired": "Claude needs to renew this login",
    "no_credentials": "Save this login again",
    "relogin_required": "Sign in again in Claude, then save it",
    "keychain_unavailable": "Unlock your credential store and retry",
    "foreign_credential": "Login details disagree",
    "api_key": "API key logins have no subscription usage",
}


def _usage(account: dict) -> dict:
    usage = account.get("usage")
    windows = []
    if account.get("usageStatus") == "ok" and isinstance(usage, dict):
        for key, label in _WINDOWS:
            window = usage.get(key)
            pct = window.get("pct") if isinstance(window, dict) else None
            if _finite(pct):
                windows.append({"label": label, "pct": max(0, min(100, pct))})
    note = None if windows else _NOTES.get(account.get("usageStatus"), "Usage not checked yet")
    return {"windows": windows, "note": note, "ready": usage_available(account)}


def _accounts(state: Snapshot) -> list[dict]:
    first_seen: dict[tuple[str, str], dict] = {}
    rows = []
    active_number = state.active["number"] if state.active else None
    for account in state.accounts:
        identity = _account_identity(account)
        original = first_seen.get(identity)
        if original is None and not account.get("disabled"):
            first_seen[identity] = account
        rows.append({
            "number": account["number"],
            "name": account.get("alias") or None,
            "email": account["email"],
            "label": account_label(account),
            "active": account["number"] == active_number,
            "disabled": bool(account.get("disabled")),
            "sameLoginAs": account_label(original) if original and not account.get("disabled") else None,
            "usage": _usage(account),
        })
    return rows


def _next_backup(state: Snapshot) -> int | None:
    """The account the quota hook would pick right now: first usable, different login."""
    if not state.active:
        return None
    current = _account_identity(state.active)
    for account in state.accounts:
        if account["number"] != state.active["number"] and _account_identity(account) != current and usage_available(account):
            return account["number"]
    return None


def _stage(state: Snapshot) -> str:
    """Where the person is in setup; the assisted view renders exactly one stage."""
    if not state.claude_path:
        return "no-claude"
    if state.settings_problem or state.hooks_disabled:
        return "blocked"
    if not state.login_email:
        return "no-login"
    if state.active is None:
        return "save-current"
    if len(state.enabled) < 2:
        return "need-second"
    if state.active.get("disabled"):
        return "pick-active"
    if not state.hook_installed:
        return "ready"
    return "armed"


def build_state(service: SessionerService, *, refresh: bool = False) -> dict:
    state = service.snapshot(refresh=refresh)
    problem = state.enable_problem()
    stage = _stage(state)
    blocked = state.settings_problem or (
        "Claude has all hooks disabled, so automatic switching cannot run. Set disableAllHooks to false in Claude settings."
        if state.hooks_disabled else None
    )
    return {
        "version": API_VERSION,
        "stage": stage,
        "login": state.login_email,
        "suggestedName": service.default_name(state) if stage == "save-current" else None,
        "accounts": _accounts(state),
        "automatic": {
            "on": state.hook_installed and not blocked,
            "canEnable": problem is None,
            "reason": str(problem) if problem else None,
            "blocked": blocked,
        },
        "backupReady": state.backup_available,
        "nextBackup": _next_backup(state),
        "settingsPath": str(service.settings_path),
    }


def _text(body: object, key: str) -> str | None:
    value = body.get(key) if isinstance(body, dict) else None
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise SessionerError("That value is not valid.")
    return value.strip() or None


def act(service: SessionerService, route: str, body: object) -> dict:
    """Run one page action and return the new state with a short confirmation."""
    if route == "/api/refresh":
        return {"message": "Usage refreshed.", "state": build_state(service, refresh=True)}
    if route == "/api/add":
        result = service.add(_text(body, "name"))
        verb = "Saved" if result.created else "Updated"
        message = f"{verb} {account_label(result.account)}."
        if result.ownership_unverified:
            message += " The ownership check was unavailable, so confirm this is the login you meant."
        return {"message": message, "state": build_state(service)}
    if route == "/api/switch":
        target = _text(body, "target")
        if target is None:
            raise SessionerError("Choose an account to switch to.")
        account, changed = service.switch(target)
        verb = "Switched to" if changed else "Already using"
        return {"message": f"{verb} {account_label(account)}.", "state": build_state(service)}
    if route == "/api/automatic":
        enabled = body.get("enabled") if isinstance(body, dict) else None
        if not isinstance(enabled, bool):
            raise SessionerError("Choose on or off.")
        service.set_automatic(enabled)
        return {
            "message": "Automatic switching is on." if enabled else "Automatic switching is off. Your saved accounts are kept.",
            "state": build_state(service),
        }
    raise KeyError(route)
