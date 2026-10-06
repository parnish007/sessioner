<h1 align="center">
  <img src="docs/assets/sessioner-logo.gif" alt="Sessioner: account patch bay for Claude Code" width="720">
</h1>

Save your Claude Code accounts, choose the active login, and turn on account switching when a usage allowance runs out. Keep working in ordinary Claude Code; Claude handles your conversation and context.

## Get started

On Windows, install [uv](https://docs.astral.sh/uv/) and Claude Code, then sign in to your first Claude account. Open PowerShell in this directory and run:

```powershell
.\setup.ps1
.\sessioner.ps1 setup
```

The guided setup saves your current login and helps you save a different backup account. It asks whether to return to your starting account and whether to turn on switching. It tells you when to use `/login` in Claude and waits until you are ready. Existing saved accounts are kept.

Prefer a window to a prompt? Run `.\sessioner.ps1 ui` to do the same setup in your browser. It walks you through each step, shows your saved accounts as a patch bay with the active login plugged in, and lets you switch with one click. It runs only on this computer.

You can leave a Claude conversation open during setup. Afterward, use `/hooks` in that conversation to confirm `StopFailure` with `rate_limit`. See [using an existing conversation](docs/existing-conversation.md) if it does not appear.

## Everyday use

```powershell
.\sessioner.ps1                 # Status and a quick action menu
.\sessioner.ps1 accounts        # Your saved accounts
.\sessioner.ps1 switch backup   # Choose an account
.\sessioner.ps1 off             # Turn off automatic switching
.\sessioner.ps1 doctor          # Find a setup problem and the next step
```

The PowerShell command works without activating an environment or changing your PATH. [All commands](docs/commands.md) include adding more accounts, turning switching back on, and checking status.

## How switching works

When Claude reports a rate limit, Sessioner checks whether the current account's usage allowance is exhausted. It changes the active login only when another enabled saved account has fresh, usable quota. A temporary throttle or unavailable usage data leaves the login unchanged.

Sessioner changes the account login. Continue or retry through Claude's normal interface; the hook cannot request another turn. [Claude's hook reference](https://code.claude.com/docs/en/hooks#stopfailure). Adoption of the new login by a running Claude process still needs a real two-account test. Read [the conversation guide](docs/existing-conversation.md) for the fallback.

## Guides

- [Getting started](docs/getting-started.md)
- [Using an existing conversation](docs/existing-conversation.md)
- [Commands and saved data](docs/commands.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Development and verification](docs/development.md)
- [Licenses](docs/credits.md)

Sessioner is [MIT licensed](LICENSE). See [third-party notices](THIRD_PARTY_NOTICES.md) for bundled component licenses and attribution.
