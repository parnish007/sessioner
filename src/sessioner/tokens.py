"""Token usage per Claude session, from the usage counters Claude already records locally.

Costs no tokens and makes no network calls. From each session record this reads only the
counters, the model name, the time and the session's folder. Message text is never kept,
shown, or written anywhere. Which account a count belongs to comes from Sessioner's own
record of login changes (`accounts/history.py`); counts from before that record began are
reported as unattributed rather than guessed.
"""

from __future__ import annotations

import json
import threading
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path

FIELDS = (
    ("input", "input_tokens"),
    ("output", "output_tokens"),
    ("cacheWrite", "cache_creation_input_tokens"),
    ("cacheRead", "cache_read_input_tokens"),
)
SESSION_LIMIT = 300


def _epoch(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _iso(value: float | None) -> str | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return None


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


class _Tally:
    __slots__ = ("counts", "messages")

    def __init__(self):
        self.counts = [0, 0, 0, 0]
        self.messages = 0

    def add(self, record: list) -> None:
        for index in range(4):
            self.counts[index] += record[3 + index]
        self.messages += 1

    def payload(self) -> dict:
        result = {name: self.counts[index] for index, (name, _) in enumerate(FIELDS)}
        result["total"] = sum(self.counts)
        result["messages"] = self.messages
        return result


class _File:
    __slots__ = ("offset", "records", "loose")

    def __init__(self):
        self.offset = 0
        self.records: dict[str, list] = {}  # message key -> [session, model, time, input, output, cacheWrite, cacheRead]
        self.loose: list[list] = []  # records with no message id to de-duplicate on


class TokenLedger:
    """Incremental reader: each call only reads what Claude has appended since the last one."""

    def __init__(self, projects_dir: Path):
        self.projects = Path(projects_dir)
        self._files: dict[str, _File] = {}
        self._owner: dict[str, str] = {}  # a resumed session repeats earlier messages in a new file; count each once
        self._folders: dict[str, str] = {}
        self._lock = threading.Lock()

    def _forget(self, name: str) -> None:
        for key in self._files.pop(name).records:
            self._owner.pop(key, None)

    def _read(self, path: Path, name: str, size: int) -> None:
        entry = self._files.get(name)
        if entry is not None and size < entry.offset:  # rewritten or truncated: start it again
            self._forget(name)
            entry = None
        if entry is None:
            entry = self._files[name] = _File()
        if size == entry.offset:
            return
        with path.open("rb") as handle:
            handle.seek(entry.offset)
            for line in handle:
                if not line.endswith(b"\n"):
                    break  # Claude is still writing this line; pick it up next time
                entry.offset += len(line)
                if b'"usage"' not in line or b'"assistant"' not in line:
                    continue
                try:
                    item = json.loads(line)
                except (UnicodeError, json.JSONDecodeError):
                    continue
                self._take(entry, name, path, item)

    def _take(self, entry: _File, name: str, path: Path, item: object) -> None:
        if not isinstance(item, dict) or item.get("type") != "assistant":
            return
        message = item.get("message")
        usage = message.get("usage") if isinstance(message, dict) else None
        if not isinstance(usage, dict):
            return
        model = message.get("model")
        if not isinstance(model, str) or not model or model.startswith("<"):
            return  # placeholders Claude writes for errors carry no real usage
        counts = [_count(usage.get(source)) for _, source in FIELDS]
        if not any(counts):
            return
        session = item.get("sessionId")
        session = session if isinstance(session, str) and session else path.stem
        folder = item.get("cwd")
        if isinstance(folder, str) and folder:
            self._folders[session] = folder
        record = [session, model, _epoch(item.get("timestamp")), *counts]
        message_id, request_id = message.get("id"), item.get("requestId")
        if not isinstance(message_id, str) or not message_id:
            entry.loose.append(record)
            return
        key = f"{message_id}:{request_id if isinstance(request_id, str) else ''}"
        owner = self._owner.setdefault(key, name)
        if owner != name:
            return
        known = entry.records.get(key)
        if known is None:
            entry.records[key] = record
        else:  # the same reply is written more than once as it streams; the largest counters are the final ones
            for index in range(3, 7):
                known[index] = max(known[index], record[index])

    def scan(self) -> None:
        found = []
        try:
            for path in self.projects.glob("**/*.jsonl"):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                found.append((stat.st_mtime, str(path), path, stat.st_size))
        except OSError:
            found = []
        found.sort()  # oldest first, so an original session owns its messages over a later copy
        present = {name for _, name, _, _ in found}
        for name in [name for name in self._files if name not in present]:
            self._forget(name)
        for _, name, path, size in found:
            try:
                self._read(path, name, size)
            except OSError:
                continue

    def report(self, *, history=(), accounts=(), live_ids=()) -> dict:
        """Totals overall, per account and per session. `history` is `accounts.history.read()`."""
        with self._lock:
            self.scan()
            times = [row[0] for row in history]
            saved = {str(account["email"]).casefold(): account for account in accounts}
            live = set(live_ids)

            def owner_of(moment: float | None) -> str | None:
                if moment is None or not times:
                    return None
                index = bisect_right(times, moment) - 1
                return history[index][1].casefold() if index >= 0 else None

            total = _Tally()
            by_account: dict[str | None, dict] = {}
            by_session: dict[str, dict] = {}
            for entry in self._files.values():
                for record in (*entry.records.values(), *entry.loose):
                    session, model, moment = record[0], record[1], record[2]
                    who = owner_of(moment)
                    total.add(record)
                    account = by_account.setdefault(who, {"tally": _Tally(), "models": {}, "sessions": set()})
                    account["tally"].add(record)
                    account["models"].setdefault(model, _Tally()).add(record)
                    account["sessions"].add(session)
                    row = by_session.setdefault(session, {"tally": _Tally(), "models": {}, "accounts": {}, "first": None, "last": None})
                    row["tally"].add(record)
                    row["models"].setdefault(model, _Tally()).add(record)
                    row["accounts"].setdefault(who, _Tally()).add(record)
                    if moment is not None:
                        row["first"] = moment if row["first"] is None else min(row["first"], moment)
                        row["last"] = moment if row["last"] is None else max(row["last"], moment)

            emails = {row[1].casefold(): row[1] for row in history}

            def identity(who: str | None) -> dict:
                if who is None:
                    return {"email": None, "number": None, "name": None, "saved": False}
                account = saved.get(who)
                if account is None:
                    return {"email": emails.get(who, who), "number": None, "name": None, "saved": False}
                return {"email": account["email"], "number": account["number"], "name": account.get("alias") or None, "saved": True}

            def models(table: dict) -> list[dict]:
                rows = [{"model": model, **tally.payload()} for model, tally in table.items()]
                return sorted(rows, key=lambda row: -row["total"])

            def split(table: dict) -> list[dict]:
                rows = [{**identity(who), **tally.payload()} for who, tally in table.items()]
                return sorted(rows, key=lambda row: -row["total"])

            account_rows = []
            for who in [*saved, *(key for key in by_account if key not in saved)]:
                data = by_account.get(who)
                account_rows.append({
                    **identity(who),
                    **(data["tally"] if data else _Tally()).payload(),
                    "models": models(data["models"]) if data else [],
                    "sessions": len(data["sessions"]) if data else 0,
                })

            session_rows = []
            for session, row in by_session.items():
                folder = self._folders.get(session, "")
                session_rows.append({
                    "sessionId": session,
                    "shortId": session[:8],
                    "project": Path(folder).name if folder else "Unknown project",
                    "cwd": folder or "Unknown folder",
                    "firstAt": _iso(row["first"]),
                    "lastAt": _iso(row["last"]),
                    "live": session in live,
                    **row["tally"].payload(),
                    "models": models(row["models"]),
                    "accounts": split(row["accounts"]),
                })
            session_rows.sort(key=lambda row: (not row["live"], -(_epoch(row["lastAt"]) or 0)))
            return {
                "available": True,
                "totals": total.payload(),
                "trackedSince": _iso(times[0]) if times else None,
                "accounts": account_rows,
                "sessions": session_rows[:SESSION_LIMIT],
                "sessionCount": len(session_rows),
            }
