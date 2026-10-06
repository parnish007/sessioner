"""A non-secret record of which saved login was active, and since when.

Claude's usage records do not say which login produced them. Sessioner notes every change of
the active login here (time, slot, email) so usage can later be attributed to the right
account, including when two accounts worked on the same session.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

FILE = "sessioner-account-history.jsonl"


def read(path: Path) -> list[tuple[float, str, int | None]]:
    """Every readable entry as (epoch seconds, email, slot), oldest first. Damaged lines are skipped."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    rows = []
    for line in text.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        at, email, number = item.get("at"), item.get("email"), item.get("number")
        if isinstance(at, bool) or not isinstance(at, (int, float)) or not isinstance(email, str) or not email:
            continue
        rows.append((float(at), email, number if isinstance(number, int) and not isinstance(number, bool) else None))
    rows.sort(key=lambda row: row[0])
    return rows


def note_active(backup_dir: Path, account: dict | None, *, now: float | None = None) -> bool:
    """Record that `account` is the active login, if that is news. Never raises."""
    email = account.get("email") if isinstance(account, dict) else None
    if not isinstance(email, str) or not email:
        return False
    path = Path(backup_dir) / FILE
    try:
        rows = read(path)
        if rows and rows[-1][1].casefold() == email.casefold():
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"at": time.time() if now is None else now, "email": email, "number": account.get("number")}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
        return True
    except OSError:
        return False
