from datetime import datetime, timezone, timedelta

import pytest

from sessioner.accounts.selection import (
    account_eligibility,
    parse_reset_at,
    select_backup,
)
from sessioner.accounts import quota_hook


NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def row(number, email, *, usage=None, age=10, disabled=False):
    return {
        "number": number,
        "email": email,
        "usageStatus": "ok" if usage is not None else "unavailable",
        "usage": usage,
        "usageAgeSeconds": age,
        **({"disabled": True} if disabled else {}),
    }


def usage(five=20, weekly=30, *, five_reset=None, weekly_reset=None, scoped=()):
    value = {
        "fiveHour": {"pct": five},
        "sevenDay": {"pct": weekly},
    }
    if five_reset is not None:
        value["fiveHour"]["resetsAt"] = iso(five_reset) if isinstance(five_reset, (int, float)) else five_reset
    if weekly_reset is not None:
        value["sevenDay"]["resetsAt"] = iso(weekly_reset) if isinstance(weekly_reset, (int, float)) else weekly_reset
    if scoped:
        value["scoped"] = list(scoped)
    return value


def iso(hours):
    return (NOW + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


def test_parse_reset_at_requires_a_future_timezone_aware_timestamp():
    assert parse_reset_at(iso(2), now=NOW) == NOW + timedelta(hours=2)
    assert parse_reset_at(NOW.isoformat(), now=NOW) is None
    assert parse_reset_at("2026-10-06T14:00:00", now=NOW) is None
    assert parse_reset_at("not-a-time", now=NOW) is None


def test_account_eligibility_reports_freshness_headroom_and_next_reset():
    account = row(1, "one@example.com", usage=usage(five=40, weekly=60, five_reset=2), age=12)
    result = account_eligibility(account, now=NOW)
    assert result.eligible is True
    assert result.reason == "available"
    assert result.headroom == pytest.approx(40)
    assert result.next_reset_at == NOW + timedelta(hours=2)


def test_scoped_limit_can_make_an_account_ineligible():
    account = row(
        1,
        "one@example.com",
        usage=usage(scoped=[{"name": "Fable", "pct": 100, "resetsAt": iso(4)}]),
    )
    result = account_eligibility(account, now=NOW)
    assert result.eligible is False
    assert result.reason == "exhausted"
    assert result.recovery_at == NOW + timedelta(hours=4)


def test_stale_disabled_and_unknown_usage_are_not_eligible():
    assert account_eligibility(row(1, "a", usage=usage(), age=601), now=NOW).reason == "stale"
    assert account_eligibility(row(2, "b", usage=usage(), disabled=True), now=NOW).reason == "disabled"
    assert account_eligibility(row(3, "c"), now=NOW).reason == "usage-unavailable"


def test_selection_prefers_earliest_known_reset_then_headroom_then_saved_order():
    accounts = [
        row(1, "active", usage=usage(five=100, weekly=100)),
        row(2, "later", usage=usage(five=20, weekly=30, five_reset=8)),
        row(3, "sooner", usage=usage(five=80, weekly=90, five_reset=2)),
        row(4, "unknown", usage=usage(five=1, weekly=2)),
    ]
    result = select_backup(accounts, active_number=1, now=NOW)
    assert result.target == 3
    assert result.reason == "earliest-reset"
    assert result.earliest_reset_at == NOW + timedelta(hours=2)

    tied = [
        row(1, "active", usage=usage(five=100, weekly=100)),
        row(2, "lower", usage=usage(five=70, weekly=70, five_reset=2)),
        row(3, "higher", usage=usage(five=20, weekly=20, five_reset=2)),
    ]
    assert select_backup(tied, active_number=1, now=NOW).target == 3


def test_duplicate_identity_and_disabled_rows_are_skipped():
    accounts = [
        row(1, "active", usage=usage(five=100, weekly=100)),
        row(2, "backup@example.com", usage=usage()),
        row(3, "BACKUP@example.com", usage=usage()),
        row(4, "disabled@example.com", usage=usage(), disabled=True),
    ]
    result = select_backup(accounts, active_number=1, now=NOW)
    assert result.target == 2


def test_all_exhausted_reports_earliest_recovery_or_unknown():
    known = [
        row(1, "active", usage=usage(five=100, weekly=100)),
        row(2, "later", usage=usage(five=100, weekly=100, five_reset=8, weekly_reset=10)),
        row(3, "sooner", usage=usage(five=100, weekly=100, five_reset=2, weekly_reset=5)),
    ]
    result = select_backup(known, active_number=1, now=NOW)
    assert result.target is None
    assert result.reason == "all-exhausted"
    assert result.all_exhausted is True
    assert result.earliest_reset_at == NOW + timedelta(hours=5)

    unknown = known[:-1] + [row(3, "unknown", usage=usage(five=100, weekly=100))]
    result = select_backup(unknown, active_number=1, now=NOW)
    assert result.target is None
    assert result.reason == "reset-unknown"
    assert result.reset_unknown is True
    assert result.earliest_reset_at is None


def test_quota_hook_uses_the_same_reset_aware_order_as_the_policy():
    accounts = [
        row(1, "active", usage=usage(five=100, weekly=100)),
        row(2, "later", usage=usage(five=10, weekly=10, five_reset=8)),
        row(3, "sooner", usage=usage(five=10, weekly=10, five_reset=2)),
    ]
    event = {
        "hook_event_name": "StopFailure",
        "error": "rate_limit",
        "error_details": "You've hit your usage limit",
    }
    assert quota_hook.choose_account(
        event,
        {"schemaVersion": 1, "activeAccountNumber": 1, "accounts": accounts},
        now=NOW,
    ) == 3
