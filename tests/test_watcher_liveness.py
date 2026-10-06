"""Enabled watcher settings are never reported as a running worker by themselves."""

import json
import os
import threading
import time

import pytest

from test_cli import row
from test_watcher import NOW, make_watcher, usage


def test_watcher_setting_alone_does_not_claim_the_worker_is_running(tmp_path):
    watcher, _, _ = make_watcher(tmp_path, [])
    watcher.set_enabled(True)
    assert watcher.status()["running"] is False


@pytest.mark.parametrize("pid,age,running", [(os.getpid(), 10, True), (os.getpid(), 21, False), (2147483647, 1, False), (os.getpid(), -5, False)])
def test_liveness_needs_both_a_recent_heartbeat_and_live_pid(tmp_path, pid, age, running):
    watcher, _, _ = make_watcher(tmp_path, [])
    watcher.set_enabled(True)
    (tmp_path / "sessioner-watcher-runtime.json").write_text(json.dumps({"pid": pid, "heartbeatAt": NOW.timestamp() - age, "runId": "0" * 32}))
    assert watcher.status()["running"] is running


def test_once_run_is_live_only_until_it_exits(tmp_path):
    watcher, _, _ = make_watcher(tmp_path, [row(1, "active@example.com", usage=usage(10))])
    watcher.set_enabled(True)
    seen = []
    assert watcher.run(once=True, on_tick=lambda state: seen.append(watcher.status()["running"])) == 0
    assert seen == [True]
    assert watcher.status()["running"] is False
    assert not (tmp_path / "sessioner-watcher-runtime.json").exists()


def test_reset_wait_keeps_heartbeat_live_and_wakes_when_disabled(tmp_path):
    watcher, _, _ = make_watcher(tmp_path, [row(1, "active@example.com", usage=usage(100, reset=8)), row(2, "backup@example.com", usage=usage(100, reset=2))])
    clock = [NOW.timestamp()]
    watcher.clock = lambda: clock[0]
    delays = []

    def sleeping(delay):
        delays.append(delay)
        assert 0 < delay <= 5
        clock[0] += delay
        assert watcher.status()["running"] is True
        if len(delays) == 6:
            watcher.set_enabled(False)

    watcher.sleeper = sleeping
    watcher.set_enabled(True)
    assert watcher.run() == 0
    assert len(delays) == 6
    assert watcher.status()["running"] is False


def test_worker_clears_runtime_after_callback_failure(tmp_path):
    watcher, _, _ = make_watcher(tmp_path, [row(1, "active@example.com", usage=usage(10))])
    watcher.set_enabled(True)

    def failed(state):
        raise RuntimeError("consumer failed")

    with pytest.raises(RuntimeError, match="consumer failed"):
        watcher.run(on_tick=failed)
    assert watcher.status()["running"] is False
    assert not (tmp_path / "sessioner-watcher-runtime.json").exists()


def test_worker_keeps_heartbeat_live_during_slow_quota_refresh(tmp_path, monkeypatch):
    watcher, _, engine = make_watcher(tmp_path, [row(1, "active@example.com", usage=usage(10))])
    watcher.set_enabled(True)
    clock = [NOW.timestamp()]
    watcher.clock = lambda: clock[0]
    monkeypatch.setattr("sessioner.watcher.HEARTBEAT_INTERVAL_SECONDS", 0.01)
    entered, release = threading.Event(), threading.Event()
    real_list = engine.list_accounts

    def slow_list(**kwargs):
        entered.set()
        assert release.wait(5)
        return real_list(**kwargs)

    engine.list_accounts = slow_list
    results = []
    worker = threading.Thread(target=lambda: results.append(watcher.run(once=True)))
    worker.start()
    try:
        assert entered.wait(5)
        clock[0] += 25
        deadline = time.monotonic() + 0.5
        while not watcher.status()["running"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert watcher.status()["running"] is True
    finally:
        release.set()
        worker.join(timeout=5)
    assert results == [0] and watcher.status()["running"] is False


def test_disabling_during_refresh_prevents_switch_and_is_not_overwritten(tmp_path):
    watcher, _, engine = make_watcher(tmp_path, [row(1, "active@example.com", usage=usage(100)), row(2, "backup@example.com", usage=usage(10))])
    watcher.set_enabled(True)
    real_list = engine.list_accounts

    def disabling_list(**kwargs):
        watcher.set_enabled(False)
        return real_list(**kwargs)

    engine.list_accounts = disabling_list
    result = watcher.tick()
    assert result["enabled"] is False and watcher.status()["enabled"] is False
    assert result["state"] == "off"
    assert not [call for call in engine.calls if call[0] == "switch"]
