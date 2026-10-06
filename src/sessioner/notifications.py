"""Small, private notification policy over Sessioner's sanitized activity."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import time


@dataclass(frozen=True)
class Notification:
    event_id: str
    title: str
    body: str


def account_name(accounts: list[dict], number: object) -> str:
    """Tray and banners use aliases or slot numbers, never credential data."""
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        return "the active account"
    account = next((item for item in accounts if item.get("number") == number), {})
    alias = account.get("alias") or account.get("name")
    if isinstance(alias, str) and alias:
        return "".join(character for character in alias if character.isprintable())[:64] or f"Account {number}"
    return f"Account {number}"


class NotificationEngine:
    """Consume events once and avoid repeated status banners for five minutes."""

    def __init__(self):
        self._initialized = False
        self._seen: deque[str] = deque(maxlen=200)
        self._recent: dict[tuple, float] = {}

    def poll(self, items: list[dict], accounts: list[dict], *, enabled: bool, now: float | None = None) -> list[Notification]:
        now = time.time() if now is None else now
        notices = []
        batch = [item for item in reversed(items[:50]) if isinstance(item, dict)]
        # Activity is latest-first. Deliver a new batch in chronological order.
        for position, item in enumerate(batch):
            identity = item.get("id")
            if not isinstance(identity, str) or not identity or len(identity) > 128 or identity in self._seen:
                continue
            self._seen.append(identity)
            if not self._initialized or not enabled:
                continue
            kind = item.get("kind")
            origin, target = item.get("from"), item.get("to")
            before, after = account_name(accounts, origin), account_name(accounts, target)
            affected = account_name(accounts, target if isinstance(target, int) and not isinstance(target, bool) and target > 0 else origin)
            if kind == "switch_failed" and any(
                later.get("kind") == "switch_confirmed" and later.get("source") == item.get("source") for later in batch[position + 1:]
            ):
                continue  # the next candidate took over; the confirmed switch is the news
            if kind == "switch_confirmed":
                title, body = "Account switched", f"Active login switched from {before} to {after}."
            elif kind == "switch_failed":
                title, body = "Account switch needs attention", "The login switch could not be confirmed. Open Sessioner to check."
            elif kind == "all_exhausted":
                title, body = "All accounts reached their limit", "Open Sessioner to see the next known quota reset."
            elif kind == "quota_available":
                title, body = "Quota is available again", f"Quota is available for {affected}."
            elif kind == "login_required":
                title, body = "Claude login needs renewal", f"Sign in again for {affected} in Claude, then save the login in Sessioner."
            else:
                continue
            if kind in {"all_exhausted", "login_required", "quota_available"}:
                signature = (kind, before, after)
                if now - self._recent.get(signature, float("-inf")) < 300:
                    continue
                self._recent[signature] = now
            notices.append(Notification(identity, title, body[:255]))
        self._initialized = True
        self._recent = {key: stamp for key, stamp in self._recent.items() if now - stamp < 300}
        return notices
