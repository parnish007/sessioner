from datetime import datetime, timezone
import json

from test_cli import Engine, row, service


def test_service_reads_live_session_metadata_from_the_active_claude_profile(tmp_path, monkeypatch):
    from sessioner.accounts import process_detection

    record = process_detection.ClaudeSession(
        pid=812,
        session_id="0123456789abcdef0123456789abcdef",
        cwd=str(tmp_path / "projects" / "demo"),
        started_at=int(datetime(2026, 10, 6, 11, 55, tzinfo=timezone.utc).timestamp() * 1000),
        kind="interactive",
        entrypoint="cli",
        status="busy",
    )
    seen = {}

    def fake_scan(claude_dir):
        seen["path"] = claude_dir
        return [record], 2

    monkeypatch.setattr(process_detection, "scan_sessions", fake_scan)
    svc = service(tmp_path, Engine([row(1, "a@example.com", "work")], login="a@example.com"))
    sessions, unreadable = svc.live_sessions()
    assert sessions == [record]
    assert unreadable == 2
    assert seen["path"] == svc.settings_path.parent


def test_session_api_state_is_metadata_only_and_marks_the_profile_account_shared(tmp_path, monkeypatch):
    from sessioner.accounts import process_detection
    from sessioner.web import api

    record = process_detection.ClaudeSession(
        pid=812,
        session_id="0123456789abcdef0123456789abcdef",
        cwd=str(tmp_path / "projects" / "demo"),
        started_at=int(datetime.now(timezone.utc).timestamp() * 1000),
        kind="interactive",
        entrypoint="cli",
        status=None,
    )
    monkeypatch.setattr(process_detection, "scan_sessions", lambda claude_dir: ([record], 1))
    engine = Engine([row(1, "a@example.com", "work")], login="a@example.com")
    state = api.build_state(service(tmp_path, engine))
    assert state["sessions"]["count"] == 1
    assert state["sessions"]["unreadable"] == 1
    assert state["sessions"]["scope"] == "shared-profile"
    item = state["sessions"]["items"][0]
    assert item["sessionId"] == record.session_id
    assert item["shortId"] == record.session_id[:8]
    assert item["status"] == "unknown"
    assert item["activeAccount"] == "work (a@example.com)"
    assert item["accountScope"] == "shared"
    payload = json.dumps(state)
    assert "transcript" not in payload.lower()
    assert "accessToken" not in payload


def test_usage_and_selection_state_expose_reset_metadata_without_changing_account_keys(tmp_path):
    from datetime import timedelta
    from sessioner.web import api

    reset = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    usage = {
        "fiveHour": {"pct": 20, "resetsAt": reset},
        "sevenDay": {"pct": 30, "resetsAt": reset},
        "scoped": [{"name": "Fable", "pct": 10, "resetsAt": reset}],
    }
    engine = Engine([
        row(1, "a@example.com", "active", usage={"fiveHour": {"pct": 100}, "sevenDay": {"pct": 100}}),
        row(2, "b@example.com", "backup", usage=usage),
    ], login="a@example.com")
    state = api.build_state(service(tmp_path, engine))
    assert state["selection"]["nextAccount"] == 2
    assert state["selection"]["reason"] in {"earliest-reset", "saved-order"}
    assert state["usageSummary"]["eligible"] == 1
    account = state["accounts"][1]
    assert set(account) == {"number", "name", "email", "label", "active", "disabled", "sameLoginAs", "usage"}
    assert account["usage"]["status"] == "ok"
    assert account["usage"]["eligible"] is True
    assert account["usage"]["windows"][-1]["label"] == "Fable"
    assert account["usage"]["windows"][0]["resetAt"] == reset[:19] + "Z"
    assert account["usage"]["windows"][0]["countdown"]
