"""Persisted privacy decisions must gate the real service read boundaries."""

from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

from sessioner.service import SessionerError
from test_cli import Engine, service


def make_service(tmp_path):
    engine = Engine()
    engine.backup_dir = tmp_path / "store"
    return service(tmp_path, engine)


def test_preferences_default_and_persist_between_service_instances(tmp_path):
    svc = make_service(tmp_path)
    assert svc.preferences().read() == {"statisticsEnabled": True, "notificationsEnabled": True}
    assert svc.preferences().update(statisticsEnabled=False) == {"statisticsEnabled": False, "notificationsEnabled": True}
    assert make_service(tmp_path).preferences().read() == {"statisticsEnabled": False, "notificationsEnabled": True}


@pytest.mark.parametrize("changes", [{"statisticsEnabled": 0}, {"notificationsEnabled": "false"}, {"credentials": "SECRET"}])
def test_preferences_reject_unknown_or_non_boolean_values_without_writing(tmp_path, changes):
    svc = make_service(tmp_path)
    with pytest.raises(SessionerError):
        svc.preferences().update(**changes)
    assert svc.preferences().read() == {"statisticsEnabled": True, "notificationsEnabled": True}
    assert not list(svc.state_dir.glob("*.json"))


def test_preference_updates_are_serialized_and_preserve_other_fields(tmp_path):
    first, second = make_service(tmp_path), make_service(tmp_path)
    first.preferences().update()
    path = first.state_dir / "sessioner-preferences.json"
    saved = json.loads(path.read_text())
    saved["futureSetting"] = "keep"
    path.write_text(json.dumps(saved))
    barrier = threading.Barrier(2)

    def change(store, key):
        barrier.wait()
        store.update(**{key: False})

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(change, first.preferences(), "statisticsEnabled"), pool.submit(change, second.preferences(), "notificationsEnabled")]
        for job in jobs:
            job.result(timeout=10)
    assert first.preferences().read() == {"statisticsEnabled": False, "notificationsEnabled": False}
    assert json.loads(path.read_text())["futureSetting"] == "keep"


def test_statistics_disabled_blocks_metadata_scans_and_ledger_construction(tmp_path, monkeypatch):
    svc = make_service(tmp_path)
    svc.preferences().update(statisticsEnabled=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("Account-only mode must not scan session or transcript files")

    monkeypatch.setattr("sessioner.accounts.process_detection.scan_sessions", forbidden)
    monkeypatch.setattr("sessioner.tokens.TokenLedger", forbidden)
    assert svc.live_sessions() == ([], 0)
    report = svc.token_report([], [])
    assert report == {
        "available": False, "reason": "statistics-disabled",
        "totals": {"input": 0, "output": 0, "cacheWrite": 0, "cacheRead": 0, "total": 0, "messages": 0},
        "trackedSince": None, "accounts": [], "sessions": [], "sessionCount": 0,
    }


def test_disabling_statistics_discards_loaded_counts(tmp_path):
    svc = make_service(tmp_path)
    assert svc.token_report([], [])["available"] is True
    assert svc._ledger is not None
    svc.preferences().update(statisticsEnabled=False)
    assert svc._ledger is None
    assert svc.token_report([], [])["available"] is False
    svc.set_preferences(statisticsEnabled=True)
    assert svc.token_report([], [])["available"] is True


def test_disable_waits_for_inflight_transcript_read(tmp_path, monkeypatch):
    svc = make_service(tmp_path)
    entered, release, disabled = threading.Event(), threading.Event(), threading.Event()

    class ReadingLedger:
        def __init__(self, path):
            pass

        def report(self, **kwargs):
            entered.set()
            assert release.wait(5)
            return {"available": True}

    monkeypatch.setattr("sessioner.tokens.TokenLedger", ReadingLedger)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(svc.token_report, [], [])
        assert entered.wait(5)

        def disable():
            svc.set_preferences(statisticsEnabled=False)
            disabled.set()

        changing = pool.submit(disable)
        assert not disabled.wait(0.1)
        release.set()
        reading.result(timeout=5)
        changing.result(timeout=5)
    assert disabled.is_set()
    assert svc._ledger is None
    assert svc.token_report([], [])["available"] is False
