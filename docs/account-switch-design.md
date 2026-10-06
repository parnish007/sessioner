# Account-switch hook

Sessioner changes the active saved Claude account when Claude Code reports exhausted usage. The user launches ordinary Claude Code. Claude Code owns the running session, context, prompts, and history.

Sessioner uses one command hook for `StopFailure` with matcher `rate_limit`. It reads saved-account usage, selects an enabled account with fresh available allowance, calls the credential switcher, and checks the resulting active account. Authentication errors and ordinary throttling do not trigger a switch. Recent account failures are held out of rotation for five minutes to avoid repeated switching on lagging usage readings.

Hook installation merges only this handler into Claude's user settings and backs up the original bytes. Removal preserves other settings and hooks. There is no terminal wrapper or transcript access. The hook does not send a prompt or ask Claude to retry a turn; Claude's own interface controls continuation after a failure.

Verification covers the quota decision, settings preservation, and isolated credential switching. Adoption of replacement credentials by the user's live Claude Code process requires a real two-account test.
