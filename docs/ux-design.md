# Sessioner terminal product

Sessioner has one purpose: manage saved Claude accounts and switch the active login when usage is exhausted. Claude Code owns the conversation. The product interface is a terminal CLI, matching the user's existing workflow.

The main interface is `sessioner`, available in this checkout through `sessioner.ps1`. A guided `setup` flow saves the current login, helps the user register a different backup login, checks that two enabled accounts exist, restores a saved starting account, and enables automatic switching. It tells the user exactly what to do in Claude without starting or controlling Claude. Existing account registration and hook installation functions provide the underlying behavior.

The commands are `setup`, `add [name]`, `accounts`, `switch [name-or-number]`, `on`, `off`, `status`, and `doctor`. Existing `enable` and `disable` spellings remain accepted. Running with no arguments shows a compact status; in an interactive terminal it offers a numbered action menu. Status and diagnostics explain missing prerequisites and show a concrete next command. Unavailable usage is displayed honestly.

The Sessioner package lives at `src/sessioner`; bundled account components stay under `vendor`. Product documentation lives under root `docs`, with a short README leading to the guided setup. License notices remain available in the license guide. This is a local product checkout, with no publication or global PATH changes as part of the UX work.

The existing account-only hook is reused. The interface never reads conversation files, stores conversation state, injects prompts, or launches another Claude process. The user can enable switching from another terminal and check `/hooks` in an existing Claude session. Request retry remains Claude Code's behavior; adoption of replacement credentials by the live process remains untested.

Tests exercise the setup flow, account registration, command routing, readiness/error messages, and hook toggles with isolated account data. Final checks run the product tests and the existing account-hook/engine regression suite, plus installed command smoke checks. No real account credentials are changed to test the UX.
