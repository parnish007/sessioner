"""A bounded, non-secret journal of account-switch decisions and outcomes."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from pathlib import Path
import re
from uuid import uuid4

from sessioner.accounts.exceptions import LockError
from sessioner.accounts.locking import FileLock
from sessioner.accounts.selection import account_eligibility
from sessioner.preferences import atomic_json, read_json


FILE = "sessioner-activity.json"
MAX_ITEMS = 200
MAX_BYTES = 262144
KINDS = frozenset({"quota_exhausted", "candidate_selected", "switch_confirmed", "switch_failed", "all_exhausted", "quota_available", "login_required"})
SOURCES = frozenset({"manual", "hook", "watcher", "desktop"})
REASONS = frozenset({
    "quota-exhausted", "verified", "manual-selection", "switch-failed", "switch-unverified", "account-busy",
    "earliest-reset", "headroom", "saved-order", "all-exhausted", "reset-unknown", "no-available-account",
    "no-active-account", "active-has-headroom", "usage-unavailable", "usage-invalid", "stale", "disabled",
    "refresh-failed", "token_expired", "no_credentials", "relogin_required", "foreign_credential",
    "credentials-unavailable", "quota-recovered",
})
LOGIN_REASONS = frozenset({"token_expired", "no_credentials", "relogin_required", "foreign_credential"})
OBSERVATIONS = LOGIN_REASONS | {"available", "exhausted"}


def _slot(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 2147483647 else None


def _allowed(value: object, choices) -> str | None:
    return value if isinstance(value, str) and value in choices else None


def _epoch(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if 0 < value <= 253402300799 and math.isfinite(value) else None
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return _epoch(moment.timestamp()) if moment.tzinfo is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def _measurement_at(account: dict, now: float) -> float | None:
    fetched = account.get("usageFetchedAt")
    if fetched is not None:
        moment = _epoch(fetched)
    else:
        age = account.get("usageAgeSeconds")
        moment = now - age if isinstance(age, (int, float)) and not isinstance(age, bool) and 0 <= age <= 300 else None
    # An invalid explicit timestamp cannot be replaced by a guessed one.
    return moment if moment is not None and 0 < moment <= now else None


def _sanitize(item: object) -> dict | None:
    if not isinstance(item, dict) or _allowed(item.get("kind"), KINDS) is None:
        return None
    identifier, at = item.get("id"), item.get("at")
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
        return None
    if not isinstance(at, str) or len(at) > 40:
        return None
    try:
        moment = datetime.fromisoformat(at.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            return None
        timestamp = moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError):
        return None
    return {
        "id": identifier, "at": timestamp, "kind": item["kind"],
        "source": _allowed(item.get("source"), SOURCES) or "manual",
        "from": _slot(item.get("from")), "to": _slot(item.get("to")),
        "reason": _allowed(item.get("reason"), REASONS),
    }


class ActivityStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _document(self) -> dict:
        value = read_json(self.path, max_bytes=MAX_BYTES)
        raw = value.get("items")
        items = [item for entry in raw[-MAX_ITEMS:] if (item := _sanitize(entry)) is not None] if isinstance(raw, list) else []
        raw_observations = value.get("observations")
        observations = {}
        if isinstance(raw_observations, dict):
            for slot, status in list(raw_observations.items())[-MAX_ITEMS:]:
                if isinstance(slot, str) and 0 < len(slot) <= 10 and slot.isascii() and slot.isdigit() and _slot(int(slot)) is not None and _allowed(status, OBSERVATIONS):
                    observations[str(int(slot))] = status
        raw_exhausted = value.get("exhaustedAt")
        exhausted = {}
        if isinstance(raw_exhausted, dict):
            for slot, at in list(raw_exhausted.items())[-MAX_ITEMS:]:
                if isinstance(slot, str) and 0 < len(slot) <= 10 and slot.isascii() and slot.isdigit() and _slot(int(slot)) is not None and isinstance(at, (int, float)) and (epoch := _epoch(at)) is not None:
                    exhausted[str(int(slot))] = epoch
        return {"items": items, "observations": observations, "exhaustedAt": exhausted}

    def exhausted_slots(self) -> list[int]:
        """Slots last seen at their limit and not yet seen to recover."""
        return sorted(int(slot) for slot in self._document()["exhaustedAt"])

    def read(self, limit: int = 50) -> dict:
        limit = max(0, min(MAX_ITEMS, limit)) if isinstance(limit, int) and not isinstance(limit, bool) else 50
        return {"items": list(reversed(self._document()["items"]))[:limit]}

    def record(self, kind: str, *, source: str = "manual", from_slot=None, to_slot=None, reason=None) -> dict | None:
        if _allowed(kind, KINDS) is None:
            return None
        moment = datetime.now(timezone.utc)
        item = _sanitize({
            "id": uuid4().hex,
            "at": moment.isoformat(timespec="milliseconds"),
            "kind": kind, "source": source, "from": from_slot, "to": to_slot, "reason": reason,
        })
        try:
            with FileLock(self.path.with_name(f".{self.path.name}.lock"), timeout=5):
                value = self._document()
                value["items"].append(item)
                value["items"] = value["items"][-MAX_ITEMS:]
                if kind == "quota_exhausted" and item["from"] is not None:
                    slot = str(item["from"])
                    value["observations"][slot] = "exhausted"
                    value["exhaustedAt"][slot] = max(value["exhaustedAt"].get(slot, 0), moment.timestamp())
                    value["observations"] = dict(list(value["observations"].items())[-MAX_ITEMS:])
                    value["exhaustedAt"] = dict(list(value["exhaustedAt"].items())[-MAX_ITEMS:])
                atomic_json(self.path, value)
        except (OSError, ValueError, LockError):
            # Diagnostics must not make a working credential transaction fail.
            return None
        return item

    def observe_accounts(self, accounts: list[dict], *, source: str = "manual", include_quota: bool = True) -> None:
        """Recovery requires quota measured after exhaustion; cached logins can still notify."""
        measured = {}
        now = datetime.now(timezone.utc).timestamp()
        for account in accounts[:MAX_ITEMS]:
            slot = _slot(account.get("number"))
            if slot is None or account.get("disabled"):
                continue
            status = _allowed(account.get("usageStatus"), LOGIN_REASONS)
            if status is None:
                eligibility = account_eligibility(account)
                status = _allowed(eligibility.reason, {"available", "exhausted"})
            if status is not None:
                measured[str(slot)] = (status, _measurement_at(account, now))
        if not measured:
            return
        try:
            with FileLock(self.path.with_name(f".{self.path.name}.lock"), timeout=5):
                value = self._document()
                changed = False
                for slot, (status, measurement_at) in measured.items():
                    previous = value["observations"].get(slot)
                    if status not in LOGIN_REASONS and not include_quota:
                        # Clear a known renewal failure after a valid login is seen again,
                        # without treating a cached quota reading as a recovery.
                        if previous in LOGIN_REASONS:
                            value["observations"].pop(slot)
                            changed = True
                        continue
                    exhausted_at = value["exhaustedAt"].get(slot)
                    kind = None
                    if status == "exhausted":
                        observed_at = measurement_at if measurement_at is not None else now
                        if exhausted_at is None or observed_at > exhausted_at:
                            value["exhaustedAt"][slot] = observed_at
                            changed = True
                    elif status == "available" and (exhausted_at is not None or previous == "exhausted"):
                        if exhausted_at is None or measurement_at is None or measurement_at <= exhausted_at:
                            continue
                        value["exhaustedAt"].pop(slot)
                        changed = True
                        kind = "quota_available"
                    if previous != status:
                        changed = True
                        value["observations"][slot] = status
                        if status in LOGIN_REASONS:
                            kind = "login_required"
                    if kind:
                        value["items"].append(_sanitize({
                            "id": uuid4().hex, "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                            "kind": kind, "source": source, "from": int(slot), "to": None,
                            "reason": status if kind == "login_required" else "quota-recovered",
                        }))
                if changed:
                    value["items"] = value["items"][-MAX_ITEMS:]
                    value["observations"] = dict(list(value["observations"].items())[-MAX_ITEMS:])
                    value["exhaustedAt"] = dict(list(value["exhaustedAt"].items())[-MAX_ITEMS:])
                    atomic_json(self.path, value)
        except (OSError, ValueError, LockError):
            return
