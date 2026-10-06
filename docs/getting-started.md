# Get started with Sessioner

You need Windows PowerShell, [uv](https://docs.astral.sh/uv/), Claude Code, and two different Claude accounts for automatic switching. Sign in to your first account using `/login` in ordinary Claude Code. An existing conversation can stay open.

Open another PowerShell window in the Sessioner directory:

```powershell
.\setup.ps1
.\sessioner.ps1 setup
```

The first command installs Sessioner locally. The second walks you through account setup. You do not need to activate an environment or change PATH.

## Follow the guided setup

1. Save the login currently active in Claude. The suggested name for your first account is `primary`.
2. When prompted, return to Claude and use `/login` to sign in to a different account. Return to Sessioner and press Enter after the login completes.
3. Save that account as your backup. If it is the same account, Sessioner tells you and lets you try the other login again.
4. Choose whether to return to your starting account, then confirm whether to turn on automatic switching. Setup makes each change after your confirmation.

Use `/login` directly between accounts. Avoid `/logout` during registration because it can revoke a login you have already saved.

Sessioner gives you the instructions; you perform the login in Claude. It does not launch Claude or control your conversation. If you already have saved accounts, setup keeps them and uses them toward the two-account requirement.

## Check that you are ready

```powershell
.\sessioner.ps1 status
```

Status shows your saved accounts, the active login, and whether automatic switching is on in your user settings. If setup is incomplete, the status message gives you the next command. For a fuller check, run:

```powershell
.\sessioner.ps1 doctor
```

In an already open Claude conversation, use `/hooks` and look for `StopFailure` with `rate_limit`. Follow [the existing-conversation guide](existing-conversation.md) if it is missing. You can also open Claude as usual with `claude` or choose a previous conversation with `claude --resume`.

## Your next commands

```powershell
.\sessioner.ps1 accounts
.\sessioner.ps1 switch primary
.\sessioner.ps1 off
.\sessioner.ps1 on
```

Run `.\sessioner.ps1` without arguments for status and an interactive action menu. See [all commands](commands.md) or [troubleshooting](troubleshooting.md) when you need another step.
