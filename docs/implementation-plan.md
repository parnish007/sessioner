# Sessioner product UX implementation plan

**Goal:** Give the account-only tool one clear Sessioner command and a guided setup flow.

**Architecture:** Sessioner's Python package presents the terminal interface and delegates account operations to the bundled account component. PowerShell helpers find the local virtual environment without changing the user's working directory. The quota hook and credential store remain the account implementation.

**Stack:** Python 3.13, argparse, the existing local engine, optional Rich terminal formatting, PowerShell, pytest.

## Product CLI

- [x] Add meaningful failing tests for `status`, `setup`, registration of two distinct logins, duplicate-login recovery, switching, toggles, and safe cancellation.
- [x] Implement `src/sessioner/__init__.py`, `__main__.py`, `cli.py`, plus focused service/setup modules as needed.
- [x] Expose `setup`, `add [name]`, `accounts`, `switch [name-or-number]`, `on`, `off`, `status`, `doctor`; retain enable/disable aliases. No arguments renders status and offers an interactive menu only when stdin is a TTY.
- [x] Delegate storage and mutations to the account switcher and public hook helpers; registration auto-assigns slots and never force-overwrites an existing account.
- [x] Show an actionable next command for missing setup, missing login, unavailable quota, malformed settings, and hook disablement.
- [x] Run product tests against isolated data, keeping all real credentials untouched.

## Packaging and launchers

- [x] Add root pyproject.toml with distribution `sessioner`, version `0.1.0`, and entry point `sessioner = sessioner.cli:main`.
- [x] Install the bundled account component and root product in setup.ps1, using the existing virtual environment.
- [x] Point sessioner.ps1 at the product CLI. Keep accounts.ps1 as a compatibility helper.
- [x] Add root MIT license/credits and ignored Python build/test artifacts. Preserve the bundled component's LICENSE exactly.
- [x] Verify setup, installed help/status/doctor, and PowerShell syntax.

## Product documentation and review

- [x] Rewrite README around `setup.ps1` then `sessioner.ps1 setup`.
- [x] Put getting-started, existing-conversation, command, troubleshooting, development, and license guides under root docs. Update child documentation to direct users to the product guides.
- [x] Independently review the final interface for scope, guidance, credential isolation, and compatibility with the existing hook.
- [x] Run product tests plus existing hook and selected account-component regression tests. Report current test results and the remaining live-handoff verification limit.
