# Use Sessioner with an existing conversation

Your conversation stays in Claude Code. Sessioner manages saved accounts and the active login; it does not move or change your conversation history. The only thing it takes from Claude's session records is the token counts shown in the browser, never the message text.

## Turn on switching while Claude is open

Leave your Claude window open. In another PowerShell window, run the guided setup from the Sessioner directory:

```powershell
.\sessioner.ps1 setup
```

If your accounts are already saved, you can turn switching on directly:

```powershell
.\sessioner.ps1 on
```

Return to Claude and open `/hooks`. Look for a `StopFailure` hook with the matcher `rate_limit`. Claude normally picks up settings changes automatically. If the hook does not appear after a few seconds, restart Claude to reload them. [Claude's hook troubleshooting](https://code.claude.com/docs/en/hooks-guide#hooks-shows-no-hooks-configured).

To reopen your own conversation after a restart, run:

```powershell
claude --resume
```

Choose the conversation you were using. If `/hooks` says that only managed hooks can run, your organization's policy prevents Sessioner's user hook from running.

## When a usage limit is reached

Sessioner reacts to `StopFailure` with `rate_limit`. It checks saved-account usage, confirms that the current allowance is exhausted, and looks for an enabled account with fresh remaining quota before changing the login. Ordinary throttling, missing or stale usage, authentication failures, and exhausted backups leave the login unchanged.

The hook does not retry the stopped request or send a prompt. Continue or retry in Claude's normal interface. `StopFailure` has no decision control and cannot force another turn. [Official hook reference](https://code.claude.com/docs/en/hooks#stopfailure).

Adoption of a replacement login by an already running Claude process remains unverified with two real accounts. If the login changed but Claude still uses the old account or reports an authentication error, check:

```powershell
.\sessioner.ps1 status
.\sessioner.ps1 doctor
```

Then restart Claude and use `claude --resume` to return to your conversation if needed. A successful account switch alone does not establish that the open process has adopted it.

## Choose an account yourself

```powershell
.\sessioner.ps1 accounts
.\sessioner.ps1 switch backup
```

You can choose by saved name or account number. Running `switch` without a name offers an account picker in an interactive terminal.

The active login is shared by ordinary Claude terminals. Use one automatic account-switching tool at a time. For manual switching only, run `.\sessioner.ps1 off`; your saved accounts remain available.
