"""A guided account-registration flow; Claude itself owns login and retry."""

from sessioner.service import SessionerError, account_label
from sessioner.display import say


class SetupCancelled(Exception):
    pass


def show_enabled(console):
    say(console, "Sessioner: Automatic switching is on in your Claude user settings.")
    say(console, "In your existing Claude conversation, check /hooks for StopFailure / rate_limit.")
    say(console, "Sessioner changes the active saved account on a confirmed usage limit. Claude owns request retry.")
    say(console, "Next: sessioner accounts to check backup usage. Confirm that Claude uses the new account after a switch.")


def show_registration(console, result):
    verb = "Saved" if result.created else "Updated"
    say(console, f"Sessioner: {verb} {account_label(result.account)}.")
    if result.ownership_unverified:
        say(console, "Sessioner: The credential ownership check was unavailable. Confirm your login and run sessioner add again when the check can complete.")


def run_setup(service, console, input_fn):
    def ask(message):
        say(console, message, end=" ")
        answer = input_fn("").strip().lower()
        if answer in ("q", "quit", "cancel"):
            raise SetupCancelled
        return answer

    def confirm(message):
        while True:
            answer = ask(message + " [Y/n/q]")
            if answer in ("", "y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            say(console, "Choose y, n, or q.")

    say(console, "Sessioner setup", style="bold")
    say(console, "Save your current login, add a different backup, then enable account switching.")
    say(console, "Keep your existing Claude conversation open. Login happens in Claude; Sessioner guides you here.")
    try:
        state = service.snapshot()
        if not state.claude_path:
            raise SessionerError("Claude Code was not found in this terminal.", "Install Claude Code or make claude available, then run sessioner setup")
        if state.settings_problem:
            raise SessionerError(state.settings_problem, "sessioner doctor")
        starting = state.active if state.active and not state.active.get("disabled") else None
        while not state.login_email:
            ask("Use /login in an existing Claude terminal, then press Enter to check again (q to finish later).")
            state = service.snapshot()
            if not state.login_email:
                say(console, "Sessioner: No login was found yet. Complete /login in this Claude profile.")
        if state.active:
            say(console, f"Sessioner: {account_label(state.active)} is already saved.")
        elif confirm(f"Save {state.login_email} as {service.default_name(state)}?"):
            result = service.add()
            show_registration(console, result)
            starting = result.account
            state = service.snapshot()
        else:
            raise SetupCancelled
        while len(state.enabled) < 2:
            say(console, "Use /login with a different account in another existing Claude terminal using the same profile.")
            ask("Press Enter after that login to check it (q to finish later).")
            state = service.snapshot()
            if not state.login_email:
                say(console, "Sessioner: No login found yet. Complete /login before checking again.")
                continue
            if state.active:
                say(console, "Sessioner: This is the same saved login; it cannot count as a different backup.")
                if state.active.get("disabled"):
                    say(console, "This saved account is disabled. Choose a different enabled login for setup.")
                continue
            if not confirm(f"Save {state.login_email} as {service.default_name(state)}?"):
                raise SetupCancelled
            show_registration(console, service.add())
            state = service.snapshot()
        if starting is None or starting.get("disabled"):
            starting = state.enabled[0]
        if not state.active or state.active["number"] != starting["number"]:
            if confirm(f"Use {account_label(starting)} as the starting account?"):
                account, _ = service.switch(str(starting["number"]))
                say(console, f"Sessioner: Starting account is {account_label(account)}.")
        if confirm("Enable automatic switching now?"):
            service.set_automatic(True)
            show_enabled(console)
        else:
            say(console, "Sessioner: Your accounts are saved. Next: sessioner on when you want automatic switching.")
        return 0
    except (SetupCancelled, EOFError, KeyboardInterrupt):
        say(console, "\nSessioner: Setup paused. Accounts saved in earlier steps are kept.")
        say(console, "Next: sessioner setup")
        return 0
