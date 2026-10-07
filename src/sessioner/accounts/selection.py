"""Pure account eligibility and reset-aware selection policy.

The browser and the StopFailure hook must answer the same question: which
saved account is safe to use next?  This module keeps that decision free of
filesystem, credential, and network access so both callers can share it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Iterable


# The account engine re-reads an idle account every 5 to 10 minutes. An idle backup's usage can only
# fall (or reset) between readings, so a reading up to 10 minutes old is still safe to switch to.
USAGE_MAX_AGE_SECONDS = 600


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def parse_reset_at(value: object, *, now: datetime) -> datetime | None:
    """Return a reliable future UTC reset time, or ``None``.

    Provider timestamps without a timezone are deliberately rejected: treating
    a local wall clock as UTC could make the hook switch or sleep at the wrong
    time.  Invalid, expired, and non-string values are simply unknown.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    parsed = parsed.astimezone(timezone.utc)
    current = now.astimezone(timezone.utc)
    return parsed if parsed > current else None


def isoformat(value: datetime | None) -> str | None:
    """Serialize a policy timestamp in the same compact UTC form as Claude."""
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class WindowStatus:
    label: str
    pct: float | None
    reset_at: datetime | None
    limiting: bool
    valid: bool = True

    @property
    def reset_at_text(self) -> str | None:
        return isoformat(self.reset_at)


@dataclass(frozen=True)
class AccountEligibility:
    number: int | None
    eligible: bool
    reason: str
    headroom: float | None
    next_reset_at: datetime | None
    recovery_at: datetime | None
    windows: tuple[WindowStatus, ...] = ()

    @property
    def next_reset_at_text(self) -> str | None:
        return isoformat(self.next_reset_at)

    @property
    def recovery_at_text(self) -> str | None:
        return isoformat(self.recovery_at)


@dataclass(frozen=True)
class SelectionResult:
    target: int | None
    reason: str
    earliest_reset_at: datetime | None = None
    all_exhausted: bool = False
    reset_unknown: bool = False
    evaluations: tuple[AccountEligibility, ...] = ()
    ranked: tuple[int, ...] = ()  # candidate slots in the order they would be tried

    @property
    def earliest_reset_at_text(self) -> str | None:
        return isoformat(self.earliest_reset_at)


def _windows(account: dict, now: datetime) -> tuple[tuple[list[WindowStatus], bool], dict]:
    usage = account.get("usage")
    if not isinstance(usage, dict):
        return ([], False), {}

    windows: list[WindowStatus] = []
    valid = True
    for key, label in (("fiveHour", "5-hour"), ("sevenDay", "Weekly")):
        if key not in usage:
            continue
        raw = usage[key]
        if not isinstance(raw, dict) or not _number(raw.get("pct")) or raw["pct"] < 0:
            valid = False
            continue
        pct = float(raw["pct"])
        windows.append(WindowStatus(
            label=label,
            pct=pct,
            reset_at=parse_reset_at(raw.get("resetsAt"), now=now),
            limiting=pct >= 100,
        ))

    scoped = usage.get("scoped", [])
    if not isinstance(scoped, list):
        valid = False
        scoped = []
    for index, raw in enumerate(scoped):
        if not isinstance(raw, dict) or not _number(raw.get("pct")) or raw["pct"] < 0:
            valid = False
            continue
        name = raw.get("name")
        label = f"{name}" if isinstance(name, str) and name else f"Model {index + 1}"
        pct = float(raw["pct"])
        windows.append(WindowStatus(
            label=label,
            pct=pct,
            reset_at=parse_reset_at(raw.get("resetsAt"), now=now),
            limiting=pct >= 100,
        ))
    return (windows, valid), usage


def account_eligibility(
    account: dict,
    *,
    now: datetime | None = None,
    max_age_seconds: float = USAGE_MAX_AGE_SECONDS,
) -> AccountEligibility:
    """Classify one account for a possible automatic handoff."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    number = account.get("number") if isinstance(account.get("number"), int) else None
    if account.get("disabled"):
        return AccountEligibility(number, False, "disabled", None, None, None)
    if account.get("usageStatus") != "ok":
        return AccountEligibility(number, False, "usage-unavailable", None, None, None)
    age = account.get("usageAgeSeconds")
    if not _number(age) or not 0 <= float(age) <= max_age_seconds:
        return AccountEligibility(number, False, "stale", None, None, None)
    (windows, valid), _ = _windows(account, now)
    if not valid:
        return AccountEligibility(number, False, "usage-invalid", None, None, None, tuple(windows))
    if not windows:
        return AccountEligibility(number, False, "usage-unavailable", None, None, None)
    headroom = 100.0 - max(window.pct for window in windows if window.pct is not None)
    limiting = [window for window in windows if window.limiting]
    next_reset = min(
        (window.reset_at for window in windows if window.reset_at is not None),
        default=None,
    )
    if limiting:
        recovery = (
            max(window.reset_at for window in limiting)
            if all(window.reset_at is not None for window in limiting)
            else None
        )
        return AccountEligibility(number, False, "exhausted", headroom, next_reset, recovery, tuple(windows))
    return AccountEligibility(number, True, "available", headroom, next_reset, None, tuple(windows))


def _identity(account: dict) -> tuple[str, str]:
    return (
        str(account.get("email") or "").strip().casefold(),
        str(account.get("organizationUuid") or "").strip().casefold(),
    )


def _candidate_reason(candidates: list[tuple[int, dict, AccountEligibility]]) -> str:
    selected = candidates[0][2]
    known = [item[2].next_reset_at for item in candidates if item[2].next_reset_at is not None]
    if selected.next_reset_at is not None and any(value != selected.next_reset_at for value in known):
        return "earliest-reset"
    if any(item[2].headroom != selected.headroom for item in candidates[1:]):
        return "headroom"
    return "saved-order"


def select_backup(
    accounts: Iterable[dict],
    *,
    active_number: int | None,
    excluded: set[int] | None = None,
    now: datetime | None = None,
) -> SelectionResult:
    """Select the same next account for the hook and browser UI.

    Account order is retained as the final tie-break, making the result stable
    for providers that do not return reset timestamps.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    excluded = excluded or set()
    rows = list(accounts)
    evaluations: list[AccountEligibility] = [account_eligibility(row, now=now) for row in rows]
    active = next((row for row in rows if row.get("number") == active_number), None)
    excluded_identities = {
        _identity(row)
        for row in rows
        if row.get("number") == active_number or row.get("number") in excluded
    }

    candidates: list[tuple[int, dict, AccountEligibility]] = []
    # Duplicate slots for the same non-active login are still usable when a
    # later slot has a fresh measurement and an earlier slot is exhausted.
    # Only the active/excluded identities are forbidden targets.
    distinct_nonactive: list[AccountEligibility] = []
    for index, (row, eligibility) in enumerate(zip(rows, evaluations)):
        number = row.get("number")
        if not isinstance(number, int) or number == active_number or number in excluded:
            continue
        identity = _identity(row)
        if identity in excluded_identities:
            continue
        distinct_nonactive.append(eligibility)
        if eligibility.eligible:
            candidates.append((index, row, eligibility))

    candidates.sort(key=lambda item: (
        0 if item[2].next_reset_at is not None else 1,
        item[2].next_reset_at or datetime.max.replace(tzinfo=timezone.utc),
        -(item[2].headroom if item[2].headroom is not None else float("-inf")),
        item[0],
    ))
    if candidates:
        selected = candidates[0][2]
        return SelectionResult(
            target=candidates[0][1]["number"],
            reason=_candidate_reason(candidates),
            earliest_reset_at=selected.next_reset_at,
            evaluations=tuple(evaluations),
            ranked=tuple(item[1]["number"] for item in candidates),
        )

    if distinct_nonactive and all(item.reason == "exhausted" for item in distinct_nonactive):
        recoveries = [item.recovery_at for item in distinct_nonactive]
        if all(value is not None for value in recoveries):
            return SelectionResult(
                target=None,
                reason="all-exhausted",
                earliest_reset_at=min(recoveries),
                all_exhausted=True,
                evaluations=tuple(evaluations),
            )
        return SelectionResult(
            target=None,
            reason="reset-unknown",
            reset_unknown=True,
            evaluations=tuple(evaluations),
        )
    return SelectionResult(target=None, reason="no-eligible-account", evaluations=tuple(evaluations))
