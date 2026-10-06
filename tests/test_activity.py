"""The switch timeline is a bounded journal of verified, non-secret outcomes."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json

import pytest

from sessioner.accounts import quota_hook
from sessioner.service import SessionerError
from test_cli import Engine, product_module, row, service
from test_switch_robustness import EVENT, three
from test_watcher import NOW, make_watcher, usage


def make_service(tmp_path, engine=None):
    engine = engine or Engine([row(1, "private@example.com", "work"), row(2, "other@example.com", "home")], login="private@example.com")
    engine.backup_dir = tmp_path / "store"
    return service(tmp_path, engine)


def test_activity_persists_safe_whitelisted_fields_latest_first(tmp_path):
    svc = make_service(tmp_path)
    svc.record_event("quota_exhausted", source="hook", from_slot=1, reason="quota-exhausted")
    svc.record_event("switch_confirmed", source="hook", from_slot=1, to_slot=2, reason="verified")
    rows = make_service(tmp_path).activity()["items"]
    assert [item["kind"] for item in rows] == ["switch_confirmed", "quota_exhausted"]
    assert len({item["id"] for item in rows}) == 2
    assert set(rows[0]) == {"id", "at", "kind", "source", "from", "to", "reason"}
    assert rows[0]["from"] == 1 and rows[0]["to"] == 2
    assert datetime.fromisoformat(rows[0]["at"].replace("Z", "+00:00")).tzinfo is not None
    assert "private@example.com" not in json.dumps(rows)


def test_journal_drops_unsafe_fields_and_invalid_event_kinds(tmp_path):
    svc = make_service(tmp_path)
    svc.record_event("SECRET unknown event", source="SECRET", from_slot="SECRET", to_slot=True, reason="accessToken=SECRET")
    assert svc.activity()["items"] == []
    svc.record_event("switch_failed", source="SECRET", from_slot="SECRET", to_slot=True, reason="accessToken=SECRET")
    item = svc.activity()["items"][0]
    assert item["from"] is None and item["to"] is None and item["reason"] is None
    assert item["source"] == "manual"
    assert "SECRET" not in (svc.state_dir / "sessioner-activity.json").read_text()


def test_journal_bounds_history_and_serializes_concurrent_writers(tmp_path):
    svc = make_service(tmp_path)
    other = make_service(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda index: (svc if index % 2 else other).record_event("candidate_selected", to_slot=index + 1), range(220)))
    items = svc.activity(limit=500)["items"]
    assert len(items) == 200
    assert len({item["id"] for item in items}) == 200
    assert len(svc.activity(limit=3)["items"]) == 3


def test_journal_read_sanitizes_corrupt_disk_entries(tmp_path):
    module = product_module("sessioner.activity")
    svc = make_service(tmp_path)
    svc.record_event("all_exhausted", reason="all-exhausted")
    path = svc.state_dir / "sessioner-activity.json"
    saved = json.loads(path.read_text())
    saved["items"][0].update({"reason": "SECRET", "source": "SECRET", "from": "SECRET", "providerMessage": "SECRET"})
    saved["items"].append({"kind": "SECRET", "id": "SECRET", "at": "SECRET"})
    path.write_text(json.dumps(saved))
    result = module.ActivityStore(path).read()
    assert len(result["items"]) == 1
    assert "SECRET" not in json.dumps(result)


def test_manual_switch_records_only_confirmed_handoffs(tmp_path):
    svc = make_service(tmp_path)
    account, changed = svc.switch("home")
    assert changed and account["number"] == 2
    assert [(item["kind"], item["source"], item["from"], item["to"]) for item in reversed(svc.activity()["items"])] == [
        ("candidate_selected", "manual", 1, 2), ("switch_confirmed", "manual", 1, 2),
    ]
    svc.switch("home")
    assert len(svc.activity()["items"]) == 2


def test_failed_switch_records_safe_failure_without_claiming_success(tmp_path):
    class Failing(Engine):
        def switch_to(self, identifier, *, json_output):
            raise RuntimeError("accessToken=SECRET private@example.com")

    svc = make_service(tmp_path, Failing([row(1, "private@example.com", "work"), row(2, "other@example.com", "home")], login="private@example.com"))
    with pytest.raises(SessionerError):
        svc.switch("home")
    items = svc.activity()["items"]
    assert items[0]["kind"] == "switch_failed" and items[0]["reason"] == "switch-failed"
    assert "switch_confirmed" not in [item["kind"] for item in items]
    assert "SECRET" not in json.dumps(items)


def test_hook_records_fallback_outcomes_and_preserves_rotation(tmp_path):
    engine = three(tmp_path / "store", bad={2})
    assert quota_hook.rotate_account(EVENT, engine, now=NOW.timestamp()) == {"status": "switched", "from": 1, "to": 3}
    svc = make_service(tmp_path)
    items = list(reversed(svc.activity()["items"]))
    assert [item["kind"] for item in items] == ["quota_exhausted", "candidate_selected", "switch_failed", "candidate_selected", "switch_confirmed"]
    assert all(item["source"] == "hook" for item in items)
    assert items[-1]["to"] == 3


def test_hook_all_exhausted_and_ordinary_throttle_have_distinct_history(tmp_path):
    engine = three(tmp_path / "store")
    engine.rows[1]["usage"]["fiveHour"]["pct"] = 100
    engine.rows[2]["usage"]["fiveHour"]["pct"] = 100
    assert quota_hook.rotate_account(EVENT, engine, now=NOW.timestamp()) == {"status": "blocked"}
    svc = make_service(tmp_path)
    assert svc.activity()["items"][0]["kind"] == "all_exhausted"
    before = svc.activity()["items"]
    engine.rows[0]["usage"]["fiveHour"]["pct"] = 10
    engine.rows[0]["usage"]["sevenDay"]["pct"] = 10
    assert quota_hook.rotate_account({"hook_event_name": "StopFailure", "error": "rate_limit"}, engine) == {"status": "ignored"}
    assert svc.activity()["items"] == before


def test_watcher_records_exhaustion_and_verified_switch_with_its_source(tmp_path):
    watcher, svc, _ = make_watcher(tmp_path, [row(1, "active@example.com", usage=usage(100)), row(2, "backup@example.com", usage=usage(10))])
    watcher.set_enabled(True)
    assert watcher.tick()["state"] == "switched"
    items = list(reversed(svc.activity()["items"]))
    assert [item["kind"] for item in items] == ["quota_exhausted", "candidate_selected", "switch_confirmed"]
    assert all(item["source"] == "watcher" for item in items)


def test_refreshed_quota_recovery_is_recorded_once_even_after_restart(tmp_path):
    engine = Engine([row(1, "private@example.com", usage=usage(100)), row(2, "other@example.com", usage=usage(100))], login="private@example.com")
    fetched = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    for account in engine.rows:
        account["usageFetchedAt"] = fetched
    svc = make_service(tmp_path, engine)
    svc.snapshot(refresh=True)
    engine.rows[1]["usage"] = usage(10)
    engine.rows[1]["usageFetchedAt"] = datetime.now(timezone.utc).isoformat()
    engine.rows[1]["usageAgeSeconds"] = 0
    svc = make_service(tmp_path, engine)
    svc.snapshot(refresh=True)
    assert [(item["kind"], item["from"], item["reason"]) for item in svc.activity()["items"]] == [("quota_available", 2, "quota-recovered")]
    svc.snapshot(refresh=True)
    assert len(svc.activity()["items"]) == 1


def test_login_renewal_is_recorded_once_without_provider_error_text(tmp_path):
    engine = Engine([row(1, "private@example.com"), row(2, "other@example.com")], login="private@example.com")
    engine.rows[1].update({"usageStatus": "relogin_required", "usageError": "SECRET renewal-token"})
    svc = make_service(tmp_path, engine)
    svc.snapshot(refresh=True)
    svc.snapshot(refresh=True)
    assert [(item["kind"], item["from"], item["reason"]) for item in svc.activity()["items"]] == [("login_required", 2, "relogin_required")]
    assert "SECRET" not in (svc.state_dir / "sessioner-activity.json").read_text()


def test_unverified_switch_does_not_record_a_success(tmp_path):
    class Unverified(Engine):
        def switch_to(self, identifier, *, json_output):
            return {"schemaVersion": 1, "switched": True, "to": {"number": int(identifier)}}

    svc = make_service(tmp_path, Unverified([row(1, "private@example.com", "work"), row(2, "other@example.com", "home")], login="private@example.com"))
    with pytest.raises(SessionerError):
        svc.switch("home")
    assert svc.activity()["items"][0]["kind"] == "switch_failed"
    assert svc.activity()["items"][0]["reason"] == "switch-unverified"
    assert "switch_confirmed" not in [item["kind"] for item in svc.activity()["items"]]


def test_failed_confirmation_read_is_recorded_as_unverified(tmp_path):
    class LostConfirmation(Engine):
        def list_accounts(self, **kwargs):
            if self.login == "other@example.com":
                raise RuntimeError("provider failure with SECRET")
            return super().list_accounts(**kwargs)

    svc = make_service(tmp_path, LostConfirmation([row(1, "private@example.com", "work"), row(2, "other@example.com", "home")], login="private@example.com"))
    with pytest.raises(SessionerError):
        svc.switch("home")
    assert svc.activity()["items"][0]["kind"] == "switch_failed"
    assert svc.activity()["items"][0]["reason"] == "switch-unverified"
    assert "SECRET" not in json.dumps(svc.activity())


def test_pathological_slot_values_on_disk_are_discarded(tmp_path):
    svc = make_service(tmp_path)
    svc.record_event("switch_confirmed")
    path = svc.state_dir / "sessioner-activity.json"
    saved = json.loads(path.read_text())
    saved["observations"] = {"9" * 5000: "exhausted"}
    path.write_text(json.dumps(saved))
    assert len(svc.activity()["items"]) == 1


def test_cached_stale_quota_cannot_create_a_recovery_event(tmp_path):
    engine = Engine([row(1, "primary@example.test", usage=usage(100)), row(2, "backup@example.test", usage=usage(100))], login="primary@example.test")
    svc = make_service(tmp_path, engine)
    svc.snapshot(refresh=True)
    engine.rows[1]["usage"] = usage(5)
    engine.rows[1]["usageAgeSeconds"] = 301
    svc.snapshot()
    assert not [item for item in svc.activity()["items"] if item["kind"] == "quota_available"]


@pytest.mark.parametrize("refresh", [False, True])
def test_unchanged_cached_measurement_before_hook_exhaustion_cannot_report_recovery(tmp_path, refresh):
    account = row(1, "primary@example.test", usage=usage(10))
    account["usageFetchedAt"] = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    svc = make_service(tmp_path, Engine([account], login="primary@example.test"))
    svc.record_event("quota_exhausted", source="hook", from_slot=1, reason="quota-exhausted")
    svc.snapshot(refresh=refresh)
    svc = make_service(tmp_path, svc.switcher)
    svc.snapshot(refresh=refresh)
    assert not [item for item in svc.activity()["items"] if item["kind"] == "quota_available"]


@pytest.mark.parametrize("timing", ["iso", "epoch", "age"])
def test_only_new_measurement_after_confirmed_exhaustion_reports_recovery_once(tmp_path, timing):
    account = row(1, "primary@example.test", usage=usage(10))
    account["usageFetchedAt"] = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    engine = Engine([account], login="primary@example.test")
    svc = make_service(tmp_path, engine)
    svc.record_event("quota_exhausted", source="hook", from_slot=1, reason="quota-exhausted")
    svc.snapshot(refresh=True)
    assert not [item for item in svc.activity()["items"] if item["kind"] == "quota_available"]
    fetched_at = datetime.now(timezone.utc)
    engine.rows[0]["usageFetchedAt"] = fetched_at.isoformat() if timing == "iso" else fetched_at.timestamp() if timing == "epoch" else None
    engine.rows[0]["usageAgeSeconds"] = 0
    svc.snapshot(refresh=True)
    svc.snapshot(refresh=True)
    recovered = [item for item in svc.activity()["items"] if item["kind"] == "quota_available"]
    assert len(recovered) == 1 and recovered[0]["from"] == 1


def test_equal_measurement_time_and_exhaustion_is_not_recovery(tmp_path):
    account = row(1, "primary@example.test", usage=usage(10))
    engine = Engine([account], login="primary@example.test")
    svc = make_service(tmp_path, engine)
    svc.record_event("quota_exhausted", source="hook", from_slot=1, reason="quota-exhausted")
    exhausted_at = json.loads((svc.state_dir / "sessioner-activity.json").read_text())["exhaustedAt"]["1"]
    engine.rows[0]["usageFetchedAt"] = exhausted_at
    engine.rows[0]["usageAgeSeconds"] = 0
    svc.snapshot(refresh=True)
    assert not [item for item in svc.activity()["items"] if item["kind"] == "quota_available"]


@pytest.mark.parametrize("fetched", [None, "broken timestamp", "2026-10-06T12:00:00", float("nan")])
def test_unknown_measurement_timing_cannot_report_recovery(tmp_path, fetched):
    account = row(1, "primary@example.test", usage=usage(10))
    account["usageFetchedAt"] = fetched
    account["usageAgeSeconds"] = None if fetched is None else 0
    svc = make_service(tmp_path, Engine([account], login="primary@example.test"))
    svc.record_event("quota_exhausted", source="hook", from_slot=1, reason="quota-exhausted")
    svc.snapshot(refresh=True)
    assert not [item for item in svc.activity()["items"] if item["kind"] == "quota_available"]


def test_cached_login_observation_clears_after_renewal_and_can_notify_again(tmp_path):
    account = row(1, "primary@example.test", usage=usage(10))
    account["usageStatus"] = "relogin_required"
    engine = Engine([account], login="primary@example.test")
    svc = make_service(tmp_path, engine)
    svc.snapshot()
    engine.rows[0]["usageStatus"] = "ok"
    svc.snapshot()
    engine.rows[0]["usageStatus"] = "relogin_required"
    svc.snapshot()
    assert len([item for item in svc.activity()["items"] if item["kind"] == "login_required"]) == 2


def test_hook_explains_a_limit_with_no_usable_backup(tmp_path):
    engine = three(tmp_path / "store")
    for slot in (1, 2):
        engine.rows[slot]["usageAgeSeconds"] = 3600  # out of date, so not a safe target
    assert quota_hook.rotate_account(EVENT, engine, now=NOW.timestamp()) == {"status": "blocked"}
    items = make_service(tmp_path).activity()["items"]
    assert [item["kind"] for item in items[:2]] == ["switch_failed", "quota_exhausted"]
    assert items[0]["reason"] == "no-available-account" and items[0]["to"] is None


def test_exhausted_slots_wait_for_a_measured_recovery(tmp_path):
    from sessioner.activity import ActivityStore

    store = ActivityStore(tmp_path / "activity.json")
    store.record("quota_exhausted", source="hook", from_slot=2, reason="quota-exhausted")
    assert store.exhausted_slots() == [2]
