"""Readable setup checks with a small, explicit set of repair actions."""

from __future__ import annotations

from sessioner.service import Snapshot


CHECK_IDS = frozenset({"claude", "accounts", "active", "automatic", "backup", "watcher"})
_LOGIN_PROBLEMS = {"token_expired", "no_credentials", "relogin_required", "foreign_credential"}


def build_health(state: Snapshot, selection, watcher: dict, desktop: dict) -> dict:
    checks = []

    def add(identifier, status, message, fix=None):
        checks.append({"id": identifier, "status": status, "message": message, "fix": fix})

    setup = {"action": "setup", "label": "Open setup"}
    add("claude", "ok" if state.claude_path else "attention",
        "Claude Code is available." if state.claude_path else "Install Claude Code, then reopen Sessioner.",
        None if state.claude_path else setup)
    enough = len(state.enabled) >= 2
    add("accounts", "ok" if enough else "attention",
        "At least two different enabled accounts are saved." if enough else "Save two different Claude accounts in setup.",
        None if enough else setup)
    active = state.active
    active_ready = bool(active and not active.get("disabled") and active.get("usageStatus") not in _LOGIN_PROBLEMS)
    if active and active.get("usageStatus") in _LOGIN_PROBLEMS:
        active_message = "The active account needs a renewed login. Sign in with Claude, then save it again."
    else:
        active_message = "The active login is saved and enabled." if active_ready else "Save the current login or select an enabled saved account."
    add("active", "ok" if active_ready else "attention", active_message, None if active_ready else setup)
    configured = state.hook_installed and not state.settings_problem and not state.hooks_disabled
    if configured:
        add("automatic", "ok", "The switching hook is configured. Check /hooks in an already open Claude session.")
    elif state.settings_problem or state.hooks_disabled:
        add("automatic", "attention", state.settings_problem or "Claude has hooks disabled. Enable hooks in Claude settings.", setup)
    else:
        add("automatic", "attention", "Automatic switching is not configured.",
            {"action": "automatic", "label": "Enable switching"} if state.enable_problem() is None else setup)
    if selection.target is not None:
        add("backup", "ok", f"Account {selection.target} has measured room for a switch.")
    elif selection.all_exhausted:
        reset = selection.earliest_reset_at_text
        message = f"All accounts are exhausted. Next known reset: {reset}." if reset else "All accounts are exhausted; the reset time is unknown."
        add("backup", "attention", message, {"action": "refresh", "label": "Refresh usage"})
    else:
        add("backup", "unknown", "No backup with fresh, usable quota is confirmed.", {"action": "refresh", "label": "Refresh usage"})
    running = watcher.get("enabled") is True and (watcher.get("running") is True or desktop.get("watcherRunning") is True)
    if running:
        add("watcher", "ok", "The reset watcher is running.")
    else:
        enabled = watcher.get("enabled") is True
        worker = desktop.get("running") is True or watcher.get("running") is True
        message = "The watcher is enabled, but no running worker is confirmed." if enabled else "The reset watcher is off."
        fix = {"action": "watcher", "label": "Enable watcher"} if worker else {
            "action": "command", "label": "Copy start command",
            "command": watcher["command"] if desktop.get("supported") is False else desktop["command"],
        }
        add("watcher", "attention" if enabled else "off", message, fix)
    required = {"claude", "accounts", "active", "automatic", "backup"}
    return {"checks": checks, "ready": all(check["status"] == "ok" for check in checks if check["id"] in required),
            "watcherRunning": running}
