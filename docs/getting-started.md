# Get started with Sessioner

You need Windows PowerShell, [uv](https://docs.astral.sh/uv/), Claude Code, and two different Claude accounts for automatic switching. Sign in to your first account using `/login` in ordinary Claude Code. An existing conversation can stay open.

Open another PowerShell window in the Sessioner directory:

```powershell
.\setup.ps1
.\desktop.ps1
```

The first command installs Sessioner locally. The second opens its dashboard and Windows tray without a separate terminal window. Use the Sessioner desktop shortcut afterward. You do not need to activate an environment or change PATH.

## Follow the guided setup

1. Save the login currently active in Claude. The suggested name for your first account is `primary`.
2. When prompted, return to Claude and use `/login` to sign in to a different account. Return to Sessioner and press Enter after the login completes.
3. Save that account as your backup. If it is the same account, Sessioner tells you and lets you try the other login again.
4. Select your starting account and enable automatic switching when both logins are saved. The terminal alternative, `.\sessioner.ps1 setup`, also offers to restore your starting account.

Use `/login` directly between accounts. Avoid `/logout` during registration because it can revoke a login you have already saved.

Sessioner gives you the instructions; you perform the login in Claude. It does not launch Claude or control your conversation. If you already have saved accounts, setup keeps them and uses them toward the two-account requirement.

## Desktop controls

Open **Switching** for setup health and a timeline of account-switch decisions and results. Each setup problem offers the relevant fix or takes you back to setup. **Start in tray** starts the tray app when the watcher is on but nothing is running it. Switch history shows each episode on one line, for example *work exhausted → home selected → Switch confirmed*, and says why when a switch failed. The watcher has separate enabled and running indicators.

Enable the reset watcher here if you want Sessioner to check for available quota in the background. The desktop app starts it while enabled. Closing the browser leaves the desktop app running; use the tray or dashboard's **Quit** to stop it. Opening the shortcut again reopens the same dashboard. Installation does not enable the watcher or start Sessioner at Windows login.

The tray offers the active account, next reset, watcher status, manual switching, refresh, and dashboard controls. Windows may place it in the taskbar's hidden-icons area. Notifications are optional; Windows notification settings can suppress them.

Turn on **Account-only privacy mode** under **Switching** to disable session and token statistics. Sessioner then avoids reading both conversation logs and session metadata. Account quota, switching, the watcher, and the activity timeline still work. Turn privacy mode off when you want session details and token counts.

Prefer the browser alone? `.\sessioner.ps1 ui` opens the dashboard without a tray or background worker. The watcher then needs `.\sessioner.ps1 watch` in a terminal.

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
