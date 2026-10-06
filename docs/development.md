# Develop Sessioner

Sessioner is a Python product with a terminal interface. Its CLI lives in `src/sessioner`; bundled account components live under `vendor/claude-swap`.

## Local installation

From the checkout root in PowerShell:

```powershell
.\setup.ps1
.\.venv\Scripts\sessioner.exe --help
```

Setup creates the local Python 3.13 environment when needed and installs both packages in editable mode. It changes neither global PATH nor the caller's working directory. The root distribution is `sessioner` version `0.1.0`; its console entry point is `sessioner.cli:main`. The bundled account distribution is `claude-swap` version `0.27.0b1`.

The root PowerShell launcher delegates to the product CLI. These are equivalent ways to invoke it:

```powershell
.\sessioner.ps1 status
.\.venv\Scripts\sessioner.exe status
.\.venv\Scripts\python.exe -m sessioner status
```

`accounts.ps1` launches advanced account tools. The bundled package also provides `sessioner-accounts`, `cswap`, and `claude-swap` entry points.

## Project boundaries

| Location | Purpose |
| --- | --- |
| `src/sessioner/` | Sessioner command routing, guided setup, and readiness messages. |
| `vendor/claude-swap/src/claude_swap/` | Saved account storage, credential switching, usage, and the quota hook. |
| `tests/` | Product behavior tested with isolated account data. |
| `vendor/claude-swap/tests/` | Account-engine and hook regression tests. |
| `docs/` | Product guides and verification notes. |
| `setup.ps1`, `sessioner.ps1`, `accounts.ps1` | Local installation and launchers. |

Registration uses unused account numbers and checks the active identity before saving it. Setup keeps existing saved accounts, requires distinct enabled accounts, and asks before restoring the starting account or enabling switching. It asks users to perform `/login` themselves; it never launches or controls Claude.

The hook changes only the account login. Keep transcript access, prompt injection, and forced retries out of this path. `StopFailure` cannot request continuation. [Official hook reference](https://code.claude.com/docs/en/hooks#stopfailure).

## Local account data

Windows account data is stored under `%USERPROFILE%\.claude-swap-backup`; existing saved accounts use the same location. Credential `.enc` files are base64-encoded, not encrypted. Account exports contain plaintext login secrets. Keep account files and exports private and out of source control.

## Verification

From the checkout root, run the product suite:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

From the checkout root, enter `vendor/claude-swap` and run the focused account-engine and hook regression suite:

```powershell
Push-Location .\vendor\claude-swap
..\..\.venv\Scripts\python.exe -m pytest tests/test_quota_hook.py tests/test_quota_hook_integration.py tests/test_cli.py tests/test_json_output.py tests/test_credentials.py tests/test_paths.py tests/test_claude_locks.py tests/test_add_account_identity.py tests/test_autoswitch.py -n 4 -q
Pop-Location
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
