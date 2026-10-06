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
| `.\sessioner.ps1 on` | Turn on automatic switching; requires two enabled saved accounts. |
| `.\sessioner.ps1 off` | Turn off automatic switching and keep your saved accounts. |
| `.\sessioner.ps1 status` | Check the active account, saved accounts, and switching configuration. |
| `.\sessioner.ps1 doctor` | Check readiness and show a concrete next step for each problem. |
| `.\sessioner.ps1 ui [--no-open] [--port N]` | Open the browser interface for guided setup, switching, and usage. |
| `.\sessioner.ps1 --help` | Show command help. |
| `.\sessioner.ps1 --version` | Show the Sessioner version. |

Square brackets indicate an optional argument; leave the brackets out of the command. `enable` and `disable` remain accepted as aliases for `on` and `off`.

Status reports Sessioner's user settings. Use `/hooks` inside Claude to confirm the hook is loaded in your conversation; project settings or an organization policy can prevent it from running.

## Browser interface

`.\sessioner.ps1 ui` opens a page in your default browser. It offers the same actions as the terminal commands: save the current login with a name, switch accounts, refresh usage, and turn automatic switching on or off. While setup is unfinished, the top card shows only the next step, with four lamps for progress. With switching on, a dashed cable shows which saved account Sessioner would use next.

The page is served from `127.0.0.1` for this one run. Each launch has its own secret link; the page rejects other websites and other computers. It shows names, emails, and usage percentages, never credentials, and it does not read conversations. Choose **Quit** in the page or press Ctrl+C in the terminal to stop it. Use `--no-open` to print the link instead of opening a browser, and `--port` for a fixed port.

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
