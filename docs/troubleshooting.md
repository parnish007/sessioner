# Troubleshooting Sessioner

Start with:

```powershell
.\sessioner.ps1 doctor
```

The check reports what is missing and the next command to run. The steps below cover common outcomes.

The dashboard's **Switching → Setup health** shows the same essential setup conditions, usable backup quota, and whether a watcher worker is actually running. Use its fix controls for configuration and usage checks.

## Sessioner will not start

Open PowerShell in the Sessioner directory and run:

```powershell
.\setup.ps1
.\sessioner.ps1 --help
```

If setup cannot find `uv`, install [uv](https://docs.astral.sh/uv/) and reopen PowerShell. If you moved the checkout or changed the local package configuration, run setup again. The PowerShell launcher works without environment activation or a PATH change.

## The watcher is enabled but stopped

Open the Sessioner desktop shortcut or run `.\desktop.ps1`. Keep the desktop app running; closing its browser tab is fine. **Quit** stops its watcher. If using browser-only mode, run `.\sessioner.ps1 watch` separately. The enabled setting alone does not prove a worker is running.

## The tray or notification is missing

Check Windows' hidden-icons area on the taskbar. Enable notifications in **Switching** and check Windows notification and Do Not Disturb settings. Windows decides whether to show a banner. A muted or suppressed notification does not affect switching or its activity timeline.

If the shortcut points to a moved checkout, run setup again from its new location to recreate it. A desktop app already running from another checkout can be closed through its tray before starting the new copy.

## Session or token details disappeared

Check **Account-only privacy mode** under **Switching**. With this on, Sessioner stops reading session metadata and conversation logs and clears cached statistics. Turn privacy mode off to read statistics again. Account quota and switching work in either mode.

## Setup cannot find a Claude login

Open ordinary Claude Code and complete `/login`. Return to Sessioner and follow the prompt, or run `.\sessioner.ps1 setup` again. Sessioner saves the active login after you complete it; it does not log in for you.

## Setup says this account is already saved

The current Claude login matches a saved account. Return to Claude, use `/login` to sign in to a different account, and press Enter in setup after it completes. Confirm the identity shown. Renaming the same login does not create a second account.

## Switching needs another account

Run `.\sessioner.ps1 setup` to save a different backup. Check `.\sessioner.ps1 accounts` for two enabled saved accounts. If an existing account is disabled, use the compatibility helper to re-enable that account: `.\accounts.ps1 enable 2`, replacing `2` with its number. Then run `.\sessioner.ps1 on`.

## Claude does not show the hook

Run `.\sessioner.ps1 on`, then open `/hooks` in Claude and look for `StopFailure` with `rate_limit`. Settings normally reload automatically. If it is still missing after a few seconds, restart Claude and use `claude --resume` to select your conversation. [Claude's hook troubleshooting](https://code.claude.com/docs/en/hooks-guide#hooks-shows-no-hooks-configured).

If Claude says only managed hooks can run, an organization policy is blocking user hooks. If doctor reports invalid Claude settings, correct the reported settings problem before running `on` again.

## A rate limit leaves the account unchanged

Check `.\sessioner.ps1 status` and `.\sessioner.ps1 accounts`. A switch needs confirmed allowance exhaustion and an enabled backup with fresh available quota. Temporary throttling, missing or stale usage, and exhausted backups do not trigger a change.

If the hook is off, run `.\sessioner.ps1 on`. If all accounts are exhausted, wait for a reset or choose your next step in Claude. Run doctor for any login or settings problem.

## The account changed but Claude is still stopped

Continue or retry through Claude's normal interface. Sessioner changes the active login; `StopFailure` cannot request a retry. [Claude's hook reference](https://code.claude.com/docs/en/hooks#stopfailure).

Live adoption of a different login by the open Claude process still needs a real two-account test. If Claude keeps the previous identity or reports an authentication error, follow [the existing-conversation guide](existing-conversation.md) and use `claude --resume` after a restart if needed.

## A saved account can no longer sign in

Use `/login` in Claude to sign in to the affected account again. Then save the login with `.\sessioner.ps1 add backup`, replacing `backup` with its saved name from `.\sessioner.ps1 accounts`. Sessioner updates the matching saved account rather than adding a duplicate.

Avoid `/logout` between registrations because it can revoke a login you saved earlier. Recheck `.\sessioner.ps1 doctor` afterward.

## Another terminal changes the active account

Ordinary Claude terminals share the active login. Use one automatic account-switching controller at a time. Turn Sessioner's automatic switching off with `.\sessioner.ps1 off` if you want manual control, then select an account with `.\sessioner.ps1 switch`.

## Report a problem

Include the OS, Claude Code version, Sessioner version, the command you ran, and the relevant error or doctor result. State whether the issue happened during setup, saving a login, turning switching on, or switching accounts. Redact account identities as needed and leave credential files, tokens, and account exports out of the report.
