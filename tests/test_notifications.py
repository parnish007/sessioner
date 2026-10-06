"""Notification policy without reading or exposing Claude message text."""

import importlib

import pytest


def engine():
    try:
        return importlib.import_module("sessioner.notifications").NotificationEngine()
    except ModuleNotFoundError:
        pytest.fail("The desktop notification policy is not implemented")


ACCOUNTS = [{"number": 1, "alias": "Primary"}, {"number": 2, "alias": "Backup"}]


def event(identity, kind, *, source="watcher", origin=1, target=2, reason=None):
    return {"id": identity, "at": "2026-10-06T12:00:00Z", "kind": kind,
            "source": source, "from": origin, "to": target, "reason": reason}


def test_startup_does_not_replay_old_switch_notifications():
    policy = engine()
    assert policy.poll([event("old", "switch_confirmed")], ACCOUNTS, enabled=True, now=100) == []
    notices = policy.poll([event("new", "switch_confirmed"), event("old", "switch_confirmed")], ACCOUNTS, enabled=True, now=101)
    assert len(notices) == 1
    assert "Primary" in notices[0].body and "Backup" in notices[0].body
    assert notices[0].event_id == "new"
    assert policy.poll([event("new", "switch_confirmed")], ACCOUNTS, enabled=True, now=102) == []


def test_disabled_notifications_are_consumed_without_backfill():
    policy = engine()
    policy.poll([], ACCOUNTS, enabled=True, now=100)
    assert policy.poll([event("disabled", "switch_failed")], ACCOUNTS, enabled=False, now=101) == []
    assert policy.poll([event("disabled", "switch_failed")], ACCOUNTS, enabled=True, now=102) == []
    assert len(policy.poll([event("enabled", "quota_available")], ACCOUNTS, enabled=True, now=103)) == 1


def test_repeated_exhaustion_is_quiet_until_the_cooldown_passes():
    policy = engine()
    policy.poll([], ACCOUNTS, enabled=True, now=0)
    assert len(policy.poll([event("a", "all_exhausted")], ACCOUNTS, enabled=True, now=1)) == 1
    assert policy.poll([event("b", "all_exhausted")], ACCOUNTS, enabled=True, now=20) == []
    assert len(policy.poll([event("c", "all_exhausted")], ACCOUNTS, enabled=True, now=400)) == 1


def test_failure_and_login_notifications_never_include_arbitrary_reason_text():
    policy = engine()
    policy.poll([], ACCOUNTS, enabled=True, now=0)
    notices = policy.poll([
        event("login", "login_required", reason="accessToken=secret"),
        event("fail", "switch_failed", reason="provider message secret"),
        event("candidate", "candidate_selected"),
    ], ACCOUNTS, enabled=True, now=1)
    assert len(notices) == 2
    assert "secret" not in " ".join(notice.body for notice in notices)
    assert all(len(notice.body) <= 255 for notice in notices)


@pytest.mark.parametrize("kind", ["quota_available", "login_required"])
def test_account_status_notification_names_the_affected_slot(kind):
    policy = engine()
    policy.poll([], ACCOUNTS, enabled=True, now=0)
    notices = policy.poll([event("affected", kind, origin=2, target=None)], ACCOUNTS, enabled=True, now=1)
    assert len(notices) == 1
    assert "Backup" in notices[0].body


def test_each_verified_switch_in_a_cycle_notifies_but_replays_do_not():
    policy = engine()
    policy.poll([], ACCOUNTS, enabled=True, now=0)
    for index, (origin, target) in enumerate([(1, 2), (2, 1), (1, 2)], 1):
        item = event(str(index), "switch_confirmed", origin=origin, target=target)
        assert len(policy.poll([item], ACCOUNTS, enabled=True, now=index)) == 1
        assert policy.poll([item], ACCOUNTS, enabled=True, now=index + 0.1) == []


def test_distinct_failed_attempts_are_not_hidden_by_status_throttling():
    policy = engine()
    policy.poll([], ACCOUNTS, enabled=True, now=0)
    assert len(policy.poll([event("attempt1", "switch_failed")], ACCOUNTS, enabled=True, now=1)) == 1
    assert len(policy.poll([event("attempt2", "switch_failed")], ACCOUNTS, enabled=True, now=2)) == 1


def test_a_failed_candidate_followed_by_a_confirmed_switch_only_announces_the_switch():
    notices = engine()
    notices.poll([], ACCOUNTS, enabled=True)
    batch = [event("c", "switch_confirmed", source="hook", target=2), event("b", "switch_failed", source="hook", target=3),
             event("a", "quota_exhausted", source="hook", target=None)]
    titles = [notice.title for notice in notices.poll(batch, ACCOUNTS, enabled=True)]
    assert titles == ["Account switched"]


def test_a_failure_with_no_later_success_still_needs_attention():
    notices = engine()
    notices.poll([], ACCOUNTS, enabled=True)
    batch = [event("b", "switch_failed", source="hook", target=3), event("a", "switch_confirmed", source="manual", target=2)]
    assert "Account switch needs attention" in [notice.title for notice in notices.poll(batch, ACCOUNTS, enabled=True)]
