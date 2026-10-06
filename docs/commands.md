# Sessioner commands

Run these commands from the Sessioner directory in PowerShell. The launcher uses the local installation, so environment activation and a PATH change are unnecessary.

| Command | What it does |
| --- | --- |
| `.\setup.ps1` | Install or update the local Sessioner installation. |
| `.\sessioner.ps1` | Show status and, in an interactive terminal, a quick action menu. |
| `.\sessioner.ps1 setup` | Guide you through saving accounts, selecting a starting login, and turning on switching. |
| `.\sessioner.ps1 add [name]` | Save the login currently active in Claude, optionally giving it a name. |
| `.\sessioner.ps1 accounts` | Show saved accounts and available usage information. |
| `.\sessioner.ps1 switch [name-or-number]` | Select a saved account, or use the interactive picker when no account is given. |
| `.\sessioner.ps1 rename <name-or-number> <new-name>` | Give a saved account a new name. The login itself does not change. |
| `.\sessioner.ps1 on` | Turn on automatic switching; requires two enabled saved accounts. |
| `.\sessioner.ps1 off` | Turn off automatic switching and keep your saved accounts. |
| `.\sessioner.ps1 status` | Check the active account, saved accounts, and switching configuration. |
| `.\sessioner.ps1 doctor` | Check readiness and show a concrete next step for each problem. |
| `.\sessioner.ps1 ui [--no-open] [--port N]` | Open the browser interface for guided setup, switching, sessions, and usage. |
| `.\sessioner.ps1 watch [--once] [--interval S]` | Run the optional reset watcher. Needs to be turned on in the browser first. |
| `.\sessioner.ps1 --help` | Show command help. |
| `.\sessioner.ps1 --version` | Show the Sessioner version. |

Square brackets indicate an optional argument; leave the brackets out of the command. `enable` and `disable` remain accepted as aliases for `on` and `off`.

Status reports Sessioner's user settings. Use `/hooks` inside Claude to confirm the hook is loaded in your conversation; project settings or an organization policy can prevent it from running.

## Browser interface

`.\sessioner.ps1 ui` opens a page in your default browser with three tabs.

- **Patch bay.** Your saved accounts are jacks hanging off the Claude Code socket, and the cord is plugged into the one in use. Drag the plug onto another account to switch, or press that account's **Switch to this** button. A dashed cord shows which account Sessioner would use next. Beside the bay are the automatic-switching switch, a summary of live sessions, and a short animation of what happens at a limit. Until setup is finished, a card above the bay tells you the one next step.
- **Details**, on every account. Gauges for the 5-hour, weekly, and any model-specific windows, each with a live countdown to its reset. Where the account stands as a backup and why. The tokens it has used, broken down by model, and the sessions it worked on. **Rename** is here too.
- **Sessions.** Every Claude session on this profile, live or ended: project and folder, tokens used (input, output, cache write, cache read), the models, and how the tokens divide between your accounts. Live sessions also show their status, PID, and running time.
- **Switching.** What happens when Claude reports a limit, the hook state, the next candidate and the order accounts would be tried in, and the optional reset watcher.

On the patch bay, pressing an account's line number switches to it. The plug also works from the keyboard: focus it, choose an account with the arrow keys, and press Enter. Esc cancels a drag. Back and Forward work, and reloading the page keeps you where you were.

If a switch can't be completed, the plug returns to the account that is still active and the page says why. A switch changes the login for every live session on the profile; retry or resume in Claude afterward.

The page is served from `127.0.0.1` for this one run. Each launch has its own secret link; the page rejects other websites and other computers. It never shows credentials. Choose **Quit** in the page or press Ctrl+C in the terminal to stop it. Use `--no-open` to print the link instead of opening a browser, and `--port` for a fixed port.

### Token counts

Token counts are read from the usage numbers Claude Code already records on this computer for each reply, so showing them costs no tokens and sends nothing anywhere. Sessioner takes only the counts, the model name, the time, and the session's folder. It never keeps, shows, or stores message text.

Claude's records don't say which login produced a reply. Sessioner keeps its own note of every change of the active login and uses the time of each reply to decide which account it belongs to. A session worked on by two accounts is divided between them. Use from before Sessioner started keeping that note is listed as **not attributed** instead of being guessed.

## Reset watcher

The watcher is optional and off by default. Turn it on under **Switching** in the browser, then start it:

```powershell
.\sessioner.ps1 watch            # keeps running; Ctrl+C stops it
.\sessioner.ps1 watch --once     # check once and exit
.\sessioner.ps1 watch --interval 120
```

While it runs it re-checks usage on a timer. If your active account is used up and another has room, it switches the saved login through the same verified switch the hook uses. If every account is at its limit it waits for the earliest known reset, or checks again later when no reset time is known. It never sends, retries, or resumes anything in Claude; after a switch you retry or resume in Claude yourself. Opening the browser page never starts the watcher, and the switch in the page only saves the setting.

## Save another account

Use `/login` in Claude to sign in to that account, then run:

```powershell
.\sessioner.ps1 add work
.\sessioner.ps1 accounts
```

Confirm the identity shown. Sessioner chooses an unused account number and detects a login you have already saved. Giving the same login another name does not make it a different backup account. Saved accounts are preserved.

## Select an account

```powershell
.\sessioner.ps1 switch work
.\sessioner.ps1 switch 2
```

Use the names and numbers shown by `accounts`. An interactive picker is available with `.\sessioner.ps1 switch`.

Usage that cannot be checked is shown as unavailable. That does not mean the account has remaining quota. Automatic switching needs fresh usable usage information before choosing a backup.

## Saved data

Your saved accounts and exports contain login secrets. Keep them private and out of source control. Storage details are in [development](development.md).

Turning switching on updates Claude's user settings, normally `%USERPROFILE%\.claude\settings.json`, and creates a settings backup. It preserves other settings and hooks. Turning it off removes Sessioner's hook and keeps the rest.

## Advanced account tools

Use `.\accounts.ps1 help` for advanced actions such as enabling or disabling an individual saved account, token checks, imports, and exports.

Those account-level enable/disable commands differ from `.\sessioner.ps1 on` and `off`, which control automatic switching as a whole. Use Sessioner's guided commands for everyday setup and switching.

The installed `sessioner` command is available inside the local Python environment. See [development](development.md) for direct invocation and package details.
