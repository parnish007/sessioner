# Develop Sessioner

Sessioner is a Python product with a terminal interface, local dashboard, and native Windows tray. Its components live under `src/sessioner`.

## Local installation

From the checkout root in PowerShell:

```powershell
.\setup.ps1
.\.venv\Scripts\sessioner.exe --help
```

Setup creates the local Python 3.13 environment when needed and installs Sessioner in editable mode. It changes neither global PATH nor the caller's working directory. The distribution is `sessioner` version `0.1.0`; its console entry points are `sessioner.cli:main` and the advanced account command `sessioner.accounts.cli:main`.

The root PowerShell launcher delegates to the product CLI. These are equivalent ways to invoke it:

```powershell
.\sessioner.ps1 status
.\.venv\Scripts\sessioner.exe status
.\.venv\Scripts\python.exe -m sessioner status
```

`accounts.ps1` launches the advanced account command.

## Project boundaries

| Location | Purpose |
| --- | --- |
| `src/sessioner/` | Sessioner command routing, guided setup, and readiness messages. |
| `src/sessioner/accounts/` | Saved account storage, credential switching, usage, the quota hook, and `selection.py`, the one policy that decides the next account for both the hook and the browser. |
| `src/sessioner/web/` | The loopback browser interface: a small standard-library server, a JSON API, and the static page. |
| `src/sessioner/watcher.py` | The optional reset watcher. |
| `src/sessioner/desktop.py`, `wintray.py`, `notifications.py` | Desktop lifecycle, native tray, and notifications. |
| `src/sessioner/preferences.py`, `activity.py`, `health.py` | Optional statistics, safe switch timeline, and setup checks. |
| `src/sessioner/tokens.py` | Token counts per session and account, from the usage counters Claude records locally. |
| `tests/` | Product behavior tested with isolated account data. |
| `docs/` | Product guides and verification notes. |
| `setup.ps1`, `desktop.ps1`, `sessioner.ps1`, `accounts.ps1` | Local installation, hidden desktop launch, and terminal launchers. |

Registration uses unused account numbers and checks the active identity before saving it. Setup keeps existing saved accounts, requires distinct enabled accounts, and asks before restoring the starting account or enabling switching. It asks users to perform `/login` themselves; it never launches or controls Claude.

The hook changes only the account login. Keep conversation content, prompt injection, and forced retries out of this path. `StopFailure` cannot request continuation. [Official hook reference](https://code.claude.com/docs/en/hooks#stopfailure).

## Local account data

Windows account data stays in the existing Claude account backup location. Sessioner stores preferences, a bounded safe activity journal, watcher configuration, quota observations, runtime heartbeats, and account-attribution history beside it. Claude's user settings, normally `%USERPROFILE%\.claude\settings.json`, hold the hook entry. These local data files belong outside source control.

With statistics enabled, session metadata is read from Claude's `sessions` folder and token statistics are extracted from conversation logs in `projects`. Sessioner never stores or displays message text or writes to these folders. Disabling statistics gates both service scanners before construction, waits for any in-flight read, and clears the ledger. The dashboard suppresses statistics requests and stale responses while disabled.

The desktop controller owns the loopback server, serialized operations worker, tray message loop, and optional watcher. Runtime files contain only liveness metadata, never the launch URL or token. Reopening a running desktop instance uses a profile-specific Windows message. Desktop and terminal watchers share a poll lock. Quitting cancels pending quota work before a new switch starts; the enabled preference is kept for next launch. Browser-only mode creates no worker. Native tray imports are safe on other platforms; desktop mode requires Windows.

Two copies of Sessioner on one machine share all of this, including the single hook entry: whichever copy last ran `on` decides which Python the hook runs. Credential `.enc` files are base64-encoded, not encrypted. Account exports contain plaintext login secrets. Keep account files and exports private and out of source control.

## Verification

From the checkout root, run the product suite:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

The account and hook checks run from the checkout root:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -n 4 -q
```

Product checks use isolated account data. They cover registration of different identities, duplicate-login recovery, existing account stores, command routing, readiness messages, cancellation, account switching, and hook toggles.

Useful command checks after installation are:

```powershell
.\sessioner.ps1 --help
.\sessioner.ps1 --version
.\sessioner.ps1 status
.\sessioner.ps1 doctor
```

`status` and `doctor` inspect the current setup; `setup`, `add`, `switch`, `on`, and `off` can change account data or Claude settings. Test mutations in temporary profiles rather than the real account store. Checks of credentials should avoid printing secret values.

Automated tests can establish correct account-store changes and hook installation. They cannot establish that a user's running Claude process adopts another real account. Live quota exhaustion, adoption of replacement credentials, and continuation require a separate real two-account test.

License details are in [Licenses](credits.md).
