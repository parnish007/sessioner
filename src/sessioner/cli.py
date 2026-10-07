"""The Sessioner terminal interface."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import math
import sys

from rich.console import Console

from sessioner import __version__
from sessioner.display import command_name, say as _say
from sessioner.service import SessionerError, SessionerService, account_label
from sessioner.setup import run_setup, show_enabled, show_registration


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise SessionerError(message, "sessioner --help")


def _parser():
    parser = Parser(prog=command_name(), description="Sessioner saves Claude accounts and switches the active login.")
    parser.add_argument("--version", action="version", version=f"Sessioner {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="command")
    commands.add_parser("setup", help="Guide you through saving accounts and enabling switching")
    add = commands.add_parser("add", help="Save the current Claude login")
    add.add_argument("name", nargs="?", help="A short account name, such as primary or backup")
    commands.add_parser("accounts", help="List saved accounts and refresh their usage")
    switch = commands.add_parser("switch", help="Choose the active saved account")
    switch.add_argument("target", nargs="?", metavar="name-or-number", help="Account name or displayed number")
    rename = commands.add_parser("rename", help="Give a saved account a new name")
    rename.add_argument("target", metavar="name-or-number", help="Current account name or displayed number")
    rename.add_argument("name", metavar="new-name", help="The new short name")
    commands.add_parser("on", aliases=["enable"], help="Enable automatic account switching")
    commands.add_parser("off", aliases=["disable"], help="Disable automatic account switching")
    commands.add_parser("status", help="Show the current account and setup state")
    commands.add_parser("doctor", help="Check prerequisites and explain what to fix")
    watch = commands.add_parser("watch", help="Run the optional reset watcher (turn it on in the browser first)")
    watch.add_argument("--once", action="store_true", help="Check once and exit")
    watch.add_argument("--interval", type=float, default=60.0, help="Seconds between checks (at least 15)")
    ui = commands.add_parser("ui", help="Open the browser interface for setup, switching, and usage")
    ui.add_argument("--no-open", action="store_true", help="Print the link instead of opening a browser")
    ui.add_argument("--port", type=int, default=0, help="Use a fixed local port instead of a free one")
    desktop = commands.add_parser("desktop", help="Open the Windows dashboard and tray companion")
    desktop.add_argument("--no-open", action="store_true", help="Start the tray without opening the browser")
    return parser


def _usage(account):
    usage = account.get("usage")
    if account.get("usageStatus") == "ok" and isinstance(usage, dict):
        cells = []
        for key, label in (("fiveHour", "5h"), ("sevenDay", "weekly")):
            window = usage.get(key)
            pct = window.get("pct") if isinstance(window, dict) else None
            if isinstance(pct, (float, int)) and not isinstance(pct, bool) and math.isfinite(pct):
                cells.append(f"{label} {pct:g}% used")
        if cells:
            return "; ".join(cells)
    notes = {
        "token_expired": "Usage unknown; Claude needs to renew this login",
        "no_credentials": "Usage unknown; save this login again with sessioner add",
        "relogin_required": "Usage unknown; use /login in Claude, then sessioner add",
        "keychain_unavailable": "Usage unknown; unlock your credential store and retry",
        "foreign_credential": "Usage unknown; login details disagree, check sessioner doctor",
        "api_key": "Subscription usage unavailable for an API key login",
    }
    return notes.get(account.get("usageStatus"), "Usage unknown; run sessioner accounts to check again")


def _accounts(console, state):
    _say(console, "Sessioner accounts", style="bold")
    if not state.accounts:
        _say(console, "No accounts saved yet. Next: sessioner setup")
        return
    for account in state.accounts:
        markers = []
        if state.active and account["number"] == state.active["number"]:
            markers.append("active")
        if account.get("disabled"):
            markers.append("disabled")
        suffix = f" [{', '.join(markers)}]" if markers else ""
        _say(console, f"  {account['number']}. {account_label(account)}{suffix}")
        _say(console, f"     {_usage(account)}")
    _say(console, "Next: sessioner switch <name-or-number>")


def _status(console, state):
    _say(console, "Sessioner", style="bold")
    if state.settings_problem:
        automatic = "Needs attention"
    elif state.hooks_disabled:
        automatic = "Blocked; Claude has all hooks disabled"
    else:
        automatic = "On in user settings" if state.hook_installed else "Off"
    _say(console, f"Automatic switching: {automatic}")
    _say(console, f"Accounts: {len(state.accounts)} saved; {len(state.enabled)} enabled")
    if state.active:
        _say(console, f"Active: {account_label(state.active)}")
        _say(console, _usage(state.active))
    elif state.login_email:
        _say(console, f"Active: {state.login_email} (not saved)")
    else:
        _say(console, "Active: No Claude login found")
    if state.active and not state.active.get("disabled") and not state.backup_available:
        _say(console, "Backup usage is unknown, expired, or exhausted. Refresh with sessioner accounts.")
    problem = state.enable_problem()
    if not state.accounts or len(state.enabled) < 2:
        next_step = "sessioner setup"
    elif problem:
        next_step = problem.next_step
    elif not state.hook_installed:
        next_step = "sessioner on"
    elif not state.backup_available:
        next_step = "sessioner accounts"
    else:
        next_step = "Check /hooks in your existing Claude conversation; sessioner accounts checks quota"
    if state.settings_problem:
        _say(console, state.settings_problem)
        next_step = "sessioner doctor"
    if state.hook_installed:
        _say(console, "Check /hooks in Claude for live hook availability; project or managed settings can override user settings.")
    _say(console, f"Next: {next_step}")


def _doctor(console, service):
    state = service.snapshot()
    _say(console, "Sessioner doctor", style="bold")
    issues = []
    if state.claude_path:
        _say(console, "OK: Claude Code is available in this terminal.")
    else:
        issues.append("Claude Code is missing. Install it or make the claude command available in this terminal.")
    if state.login_email:
        _say(console, f"OK: Current Claude login is {state.login_email}.")
    else:
        issues.append("No current Claude login. Use /login in an existing Claude terminal.")
    if len(state.enabled) >= 2:
        _say(console, f"OK: {len(state.enabled)} different enabled accounts are saved.")
    else:
        issues.append("Two different enabled accounts are required. Run sessioner setup.")
    if state.login_email and (not state.active or state.active.get("disabled")):
        issues.append("The current login is not saved and enabled. Run sessioner add or sessioner switch <name-or-number>.")
    if state.settings_problem:
        issues.append(state.settings_problem)
    elif state.hooks_disabled:
        issues.append(f"All Claude hooks are disabled. Set disableAllHooks to false in {service.settings_path}.")
    elif state.hook_installed:
        _say(console, "OK: Sessioner is configured in Claude user settings for StopFailure / rate_limit.")
    else:
        issues.append("Automatic switching is off. After saving your accounts, run sessioner on.")
    if state.active and not state.active.get("disabled") and len(state.enabled) >= 2 and not state.backup_available:
        issues.append("Backup usage is unknown, stale, or exhausted. Run sessioner accounts; Sessioner needs fresh available backup quota.")
    for issue in issues:
        _say(console, f"Fix: {issue}")
    _say(console, "Check /hooks in your existing Claude conversation; project and managed settings can override user hooks.")
    _say(console, "Next: sessioner doctor after fixes" if issues else "Next: sessioner accounts; verify a live handoff in Claude when a usage limit occurs")
    return 1 if issues else 0


_WATCH_LINES = {
    "armed": "The active account has room. Watching.",
    "waiting": "Every saved account is at its limit. Waiting for the earliest reset.",
    "reset_unknown": "Every saved account is at its limit and no reset time is known. Checking again later.",
    "off": "The watcher was turned off. Stopping.",
}


def _watch_line(state):
    name = state.get("state")
    if name == "switched":
        return f"Switched from account {state.get('lastSwitchFrom')} to {state.get('lastSwitchTo')}. Retry or resume in Claude; Sessioner never does."
    if name == "blocked":
        return f"Blocked ({state.get('reason') or 'unknown'}). The active account was left as it is."
    return _WATCH_LINES.get(name, "Checked.")


def _watch(console, service, args):
    if not math.isfinite(args.interval) or args.interval <= 0:
        raise SessionerError("The watcher interval must be greater than zero.", "sessioner watch --interval 60")
    if args.interval < 15:
        raise SessionerError("Use an interval of at least 15 seconds so usage is not checked too often.", "sessioner watch --interval 60")
    watcher = service.watcher()
    if not watcher.status().get("enabled"):
        _say(console, "Sessioner: The reset watcher is off, so nothing was checked or changed.")
        _say(console, "Next: turn it on in the browser (sessioner ui, Switching details), then run sessioner watch")
        return 0
    if not args.once:
        _say(console, "Sessioner: Watcher is on. Press Ctrl+C to stop. It switches the saved login only.")
    return watcher.run(once=args.once, interval=args.interval, on_tick=lambda state: _say(console, f"Sessioner: {_watch_line(state)}"))


def _dispatch(args, service, console, input_fn, interactive):
    command = args.command
    if command == "setup":
        if not interactive:
            raise SessionerError("Setup needs an interactive terminal.", "Run sessioner setup in a terminal; sessioner add and sessioner on also work directly")
        return run_setup(service, console, input_fn)
    if command == "accounts":
        _accounts(console, service.snapshot(refresh=True, force=True))
    elif command == "add":
        show_registration(console, service.add(args.name))
        _say(console, "Next: sessioner accounts, or sessioner setup to add a backup")
    elif command == "switch":
        target = args.target
        if target is None:
            if not interactive:
                raise SessionerError("Choose an account to switch to.", "sessioner accounts, then sessioner switch <name-or-number>")
            _accounts(console, service.snapshot())
            _say(console, "Account name or number (Enter to cancel):", end=" ")
            target = input_fn("").strip()
            if not target:
                _say(console, "Sessioner: Cancelled.")
                return 0
        account, changed = service.switch(target)
        _say(console, f"Sessioner: Switched to {account_label(account)}." if changed else f"Sessioner: {account_label(account)} is already active.")
    elif command == "rename":
        account = service.rename(args.target, args.name)
        _say(console, f"Sessioner: Renamed to {account_label(account)}.")
    elif command in ("on", "enable"):
        service.set_automatic(True)
        show_enabled(console)
    elif command in ("off", "disable"):
        service.set_automatic(False)
        _say(console, "Sessioner: Automatic switching is off. Your saved accounts remain available.")
        _say(console, "Next: sessioner switch <name-or-number>, or sessioner on")
    elif command == "doctor":
        return _doctor(console, service)
    elif command == "watch":
        return _watch(console, service, args)
    elif command == "ui":
        from sessioner.web.server import run_ui
        return run_ui(service, console, open_browser=not getattr(args, "no_open", False), port=getattr(args, "port", 0))
    elif command == "desktop":
        from sessioner.desktop import run_desktop
        return run_desktop(service, open_browser=not args.no_open)
    else:
        state = service.snapshot()
        _status(console, state)
        if command is None and interactive:
            toggle = "off" if state.hook_installed else "on"
            _say(console, f"\n1. Guided setup\n2. Saved accounts\n3. Switch account\n4. Save current account\n5. Turn switching {toggle}\n6. Doctor\n0. Exit")
            _say(console, "Choose an action [0]:", end=" ")
            choice = input_fn("").strip()
            if choice in ("", "0", "q"):
                return 0
            commands = {"1": "setup", "2": "accounts", "3": "switch", "4": "add", "5": toggle, "6": "doctor", "7": "ui"}
            selected = commands.get(choice)
            if not selected:
                raise SessionerError("Choose a menu number from 0 to 7.", "sessioner")
            return _dispatch(argparse.Namespace(command=selected, name=None, target=None), service, console, input_fn, interactive)
    return 0


def main(argv=None, service=None, console=None, input_fn=None):
    """Run the CLI; engine, output, and input can be supplied by embedders."""
    terminal_output = bool(getattr(sys.stdout, "isatty", lambda: False)())
    console = console or Console(highlight=False, markup=False, color_system="auto" if terminal_output else None, no_color=not terminal_output)
    input_fn = input_fn or input
    interactive = bool(getattr(sys.stdin, "isatty", lambda: False)())
    try:
        printed = io.StringIO()
        parser_exit = None
        with redirect_stdout(printed), redirect_stderr(printed):
            try:
                args = _parser().parse_args(argv)
            except SystemExit as exc:
                parser_exit = int(exc.code or 0)
        if parser_exit is not None:
            _say(console, printed.getvalue().rstrip())
            return parser_exit
        service = service if service is not None else SessionerService()
        return _dispatch(args, service, console, input_fn, interactive)
    except SessionerError as exc:
        _say(console, f"Sessioner: {exc}")
        _say(console, f"Next: {exc.next_step}")
        return 1
    except (EOFError, KeyboardInterrupt):
        _say(console, "\nSessioner: Cancelled. Next: sessioner status")
        return 0
    except Exception:
        _say(console, "Sessioner: The command could not finish. Next: sessioner doctor")
        return 1
