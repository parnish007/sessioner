"""Browser-facing view of Sessioner state and the few actions the page may take.

The page never receives credentials, login tokens, or message text: only account
names, emails, usage percentages, and the stage of setup.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sessioner.accounts.selection import account_eligibility, isoformat, parse_reset_at, select_backup
from sessioner.display import command_name
from sessioner.health import CHECK_IDS, build_health
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


def _countdown(reset_at: datetime | None, now: datetime) -> str | None:
    if reset_at is None:
        return None
    seconds = max(0, int((reset_at - now).total_seconds()))
    if seconds < 60:
        return "<1 min"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    hours, remainder = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {remainder:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def _window(label: str, raw: object, now: datetime) -> dict | None:
    if not isinstance(raw, dict) or not _finite(raw.get("pct")) or raw["pct"] < 0:
        return None
    reset = parse_reset_at(raw.get("resetsAt"), now=now)
    result = {"label": label, "pct": max(0, min(100, float(raw["pct"]))) }
    if reset is not None:
        result["resetAt"] = isoformat(reset)
        result["countdown"] = _countdown(reset, now)
    else:
        result["resetAt"] = None
        result["countdown"] = None
    return result


def _usage(account: dict, *, now: datetime) -> dict:
    usage = account.get("usage")
    windows = []
    if account.get("usageStatus") == "ok" and isinstance(usage, dict):
        for key, label in _WINDOWS:
            item = _window(label, usage.get(key), now)
            if item is not None:
                windows.append(item)
        scoped = usage.get("scoped", [])
        if isinstance(scoped, list):
            for index, item in enumerate(scoped):
                name = item.get("name") if isinstance(item, dict) else None
                label = name if isinstance(name, str) and name else f"Model {index + 1}"
                rendered = _window(label, item, now)
                if rendered is not None:
                    windows.append(rendered)
    eligibility = account_eligibility(account, now=now)
    note = None if windows else _NOTES.get(account.get("usageStatus"), "Usage not checked yet")
    age = account.get("usageAgeSeconds")
    return {
        "windows": windows,
        "note": note,
        "ready": usage_available(account),
        "status": account.get("usageStatus") or "unknown",
        "ageSeconds": age if _finite(age) else None,
        "fetchedAt": account.get("usageFetchedAt") if isinstance(account.get("usageFetchedAt"), str) else None,
        "eligible": eligibility.eligible,
        "eligibilityReason": eligibility.reason,
        "headroom": eligibility.headroom,
        "nextResetAt": eligibility.next_reset_at_text,
        "recoveryAt": eligibility.recovery_at_text,
    }


def _accounts(state: Snapshot, *, now: datetime) -> list[dict]:
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
            "usage": _usage(account, now=now),
        })
    return rows


def _session_state(service: SessionerService, state: Snapshot, *, now: datetime, enabled: bool = True) -> dict:
    sessions, unreadable = service.live_sessions() if enabled else ([], 0)
    active = account_label(state.active) if state.active else state.login_email
    items = []
    for session in sessions:
        session_id = session.session_id if isinstance(session.session_id, str) else ""
        cwd = session.cwd if isinstance(session.cwd, str) else ""
        started = None
        elapsed = None
        if isinstance(session.started_at, (int, float)) and not isinstance(session.started_at, bool) and session.started_at > 0:
            try:
                started_dt = datetime.fromtimestamp(session.started_at / 1000, tz=timezone.utc)
                started = started_dt.isoformat(timespec="seconds").replace("+00:00", "Z")
                elapsed = max(0, int((now - started_dt).total_seconds()))
            except (OverflowError, OSError, ValueError):
                pass
        status = session.status if session.status in {"busy", "idle", "waiting"} else "unknown"
        items.append({
            "sessionId": session_id,
            "shortId": session_id[:8] if session_id else "unknown",
            "pid": session.pid,
            "project": Path(cwd).name if cwd else "Unknown project",
            "cwd": cwd or "Unknown folder",
            "startedAt": started,
            "elapsedSeconds": elapsed,
            "kind": session.kind or "unknown",
            "entrypoint": session.entrypoint or "unknown",
            "status": status,
            "activeAccount": active,
            "accountScope": "shared",
        })
    return {
        "enabled": enabled,
        "items": items,
        "count": len(items),
        "unreadable": unreadable,
        "scope": "shared-profile",
        "activeAccount": active,
    }


_WATCHER_STATES = {"off", "armed", "waiting", "reset_unknown", "blocked", "switched"}


def _slot(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _epoch_text(value: object) -> str | None:
    if not _finite(value) or value <= 0:
        return None
    try:
        return isoformat(datetime.fromtimestamp(value, tz=timezone.utc))
    except (OverflowError, OSError, ValueError):
        return None


def _watcher_state(service: SessionerService) -> dict:
    """Browser-safe watcher state: whitelisted values only, never the raw file."""
    raw = service.watcher().status()
    state = raw.get("state") if raw.get("state") in _WATCHER_STATES else "off"
    reason = raw.get("reason")
    return {
        "enabled": raw.get("enabled") is True,
        "running": raw.get("running") is True,
        "state": state,
        "reason": reason if isinstance(reason, str) and len(reason) <= 40 else None,
        "lastPollAt": _epoch_text(raw.get("lastPollAt")),
        "nextPollAt": _epoch_text(raw.get("nextPollAt")),
        "lastSwitchFrom": _slot(raw.get("lastSwitchFrom")),
        "lastSwitchTo": _slot(raw.get("lastSwitchTo")),
        "command": f"{command_name()} watch",
    }


def _desktop_state(service: SessionerService) -> dict:
    from sessioner.desktop import status_for, supported

    raw = status_for(service.state_dir)
    return {
        "supported": supported(),
        "running": raw.get("running") is True,
        "watcherRunning": raw.get("watcherRunning") is True,
        "pid": _slot(raw.get("pid")),
        "heartbeatAt": _epoch_text(raw.get("heartbeatAt")),
        "command": f"{command_name()} desktop",
    }


def _selection_payload(result) -> dict:
    return {
        "nextAccount": result.target,
        "reason": result.reason,
        "earliestResetAt": result.earliest_reset_at_text,
        "allExhausted": result.all_exhausted,
        "resetUnknown": result.reset_unknown,
        "ranked": list(result.ranked),
    }


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
    service.note_active(state.active)  # also catches a login changed outside Sessioner
    now = datetime.now(timezone.utc)
    selection = select_backup(
        state.accounts,
        active_number=state.active.get("number") if state.active else None,
        now=now,
    )
    accounts = _accounts(state, now=now)
    preferences = service.preferences().read()
    sessions = _session_state(service, state, now=now, enabled=preferences["statisticsEnabled"])
    watcher = _watcher_state(service)
    desktop = _desktop_state(service)
    usage_rows = [account["usage"] for account in accounts]
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
        "accounts": accounts,
        "automatic": {
            "on": state.hook_installed and not blocked,
            "canEnable": problem is None,
            "reason": str(problem) if problem else None,
            "blocked": blocked,
        },
        "backupReady": selection.target is not None,
        "nextBackup": selection.target,
        "sessions": sessions,
        "selection": _selection_payload(selection),
        "watcher": watcher,
        "desktop": desktop,
        "preferences": preferences,
        "activity": service.activity(),
        "health": build_health(state, selection, watcher, desktop),
        "usageSummary": {
            "accounts": len(accounts),
            "eligible": sum(1 for row in usage_rows if row.get("eligible")),
            "exhausted": sum(1 for row in usage_rows if row.get("eligibilityReason") == "exhausted"),
            "earliestResetAt": selection.earliest_reset_at_text,
        },
        "settingsPath": str(service.settings_path),
    }


def token_inputs(service: SessionerService) -> tuple[list[dict], list[str]]:
    """The quick part of a token report; the server gathers this under its lock."""
    if not service.preferences().read()["statisticsEnabled"]:
        return [], []
    state = service.snapshot()
    service.note_active(state.active)
    sessions, _ = service.live_sessions()
    return state.accounts, [session.session_id for session in sessions if isinstance(session.session_id, str)]


def build_tokens(service: SessionerService, accounts: list[dict], live_ids: list[str]) -> dict:
    """Counts, model names, times and folders only; never message text."""
    return {"tokens": service.token_report(accounts, live_ids)}


def _text(body: object, key: str) -> str | None:
    value = body.get(key) if isinstance(body, dict) else None
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise SessionerError("That value is not valid.")
    return value.strip() or None


def act(service: SessionerService, route: str, body: object) -> dict:
    """Run one page action and return the new state with a short confirmation."""
    if route == "/api/preferences":
        allowed = {"statisticsEnabled", "notificationsEnabled"}
        if not isinstance(body, dict) or not body or not set(body) <= allowed or any(type(value) is not bool for value in body.values()):
            raise SessionerError("Choose on or off for each preference.")
        service.set_preferences(**body)
        from sessioner.desktop import wake_for
        wake_for(service.state_dir)
        return {"message": "Preferences saved.", "state": build_state(service)}
    if route == "/api/health/fix":
        if not isinstance(body, dict) or set(body) != {"check"} or not isinstance(body["check"], str) or body["check"] not in CHECK_IDS:
            raise SessionerError("Choose one of the setup checks shown in Sessioner.")
        check = body["check"]
        if check == "automatic":
            service.set_automatic(True)
            message = "Automatic switching is configured. Check /hooks in your open Claude session."
        elif check == "backup":
            return {"message": "Usage refreshed.", "state": build_state(service, refresh=True)}
        elif check == "watcher":
            from sessioner.desktop import launch_detached, wake_for
            service.watcher().set_enabled(True)
            desktop = _desktop_state(service)
            if desktop["running"] or service.watcher().status().get("running") is True:
                wake_for(service.state_dir)
                message = "Reset watcher is on. Its status updates in a few seconds."
            elif not desktop["supported"]:
                message = f"Reset watcher is on. Run {command_name()} watch to start it."
            elif launch_detached():
                message = "Reset watcher is on and the Sessioner tray app is starting."
            else:
                message = f"Reset watcher is on, but the tray app could not start. Run {desktop['command']}."
        else:
            message = "Open Setup and follow the account or Claude installation steps."
        return {"message": message, "state": build_state(service)}
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
    if route == "/api/rename":
        target, name = _text(body, "target"), _text(body, "name")
        if target is None or name is None:
            raise SessionerError("Type a new name for the account.")
        account = service.rename(target, name)
        return {"message": f"Renamed to {account_label(account)}.", "state": build_state(service)}
    if route == "/api/automatic":
        enabled = body.get("enabled") if isinstance(body, dict) else None
        if not isinstance(enabled, bool):
            raise SessionerError("Choose on or off.")
        service.set_automatic(enabled)
        return {
            "message": "Automatic switching is on." if enabled else "Automatic switching is off. Your saved accounts are kept.",
            "state": build_state(service),
        }
    if route == "/api/watcher":
        enabled = body.get("enabled") if isinstance(body, dict) else None
        if not isinstance(enabled, bool):
            raise SessionerError("Choose on or off.")
        service.watcher().set_enabled(enabled)
        from sessioner.desktop import wake_for
        wake_for(service.state_dir)
        message = (
            f"Reset watcher is enabled. Keep Sessioner desktop or {command_name()} watch running; browser-only mode does not start it."
            if enabled else "Reset watcher is off."
        )
        return {"message": message, "state": build_state(service)}
    raise KeyError(route)
