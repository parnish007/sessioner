"""Account-only hook integration against cswap's actual file switch backend."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from tests.test_autoswitch import EngineHarness, _entry_for, _usage


def hook_module():
    return importlib.import_module("claude_swap.quota_hook")


@pytest.fixture
def account_store(temp_home, monkeypatch):
    """Real isolated account files; only remote quota observations are faked."""
    harness = EngineHarness(temp_home)
    harness.seed(1, "first@example.com")
    harness.seed(2, "second@example.com")
    harness.make_live("first@example.com", 1)
    observations = {"1": _usage(100), "2": _usage(20)}

    def entries(accounts_info, fetch=None):
        return {
            str(account[0]): _entry_for(
                observations[str(account[0])], harness.clock.now
            )
            for account in accounts_info
        }

    monkeypatch.setattr(harness.switcher, "_collect_usage_entries", entries)

    def forbidden_external_call(*args, **kwargs):
        pytest.fail("The account-only hook must not launch a process or call the network")

    monkeypatch.setattr("subprocess.Popen", forbidden_external_call)
    monkeypatch.setattr("urllib.request.urlopen", forbidden_external_call)
    return harness, observations


def credentials(harness):
    return harness.temp_home / ".claude" / ".credentials.json"


def quota_failure(**extra):
    return {
        "hook_event_name": "StopFailure",
        "error": "rate_limit",
        **extra,
    }


def test_rotation_changes_real_credentials_and_preserves_claude_files(account_store, monkeypatch):
    harness, _ = account_store
    transcript = harness.temp_home / ".claude" / "projects" / "existing" / "conversation.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript_bytes = b'{"type":"user","message":"Existing Claude conversation"}\n'
    transcript.write_bytes(transcript_bytes)
    config = harness.temp_home / ".claude.json"
    original_config = json.loads(config.read_text())
    original_config["projects"] = {"existing-project": {"allowedTools": ["Read"]}}
    config.write_text(json.dumps(original_config))
    original_open = Path.open

    def no_transcript_access(path, *args, **kwargs):
        if path.absolute().is_relative_to(transcript.parent.absolute()):
            pytest.fail("The account-only hook must not read or write conversation files")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", no_transcript_access)
    result = hook_module().rotate_account(
        quota_failure(transcript_path=str(transcript), session_id="owned-by-claude"),
        switcher=harness.switcher,
        now=harness.clock.now,
    )
    assert result["status"] == "switched"
    assert harness.active_number() == 2
    assert harness.switcher.status(json_output=True)["active"]["number"] == 2
    assert json.loads(credentials(harness).read_text())["claudeAiOauth"]["accessToken"] == "sk-2"
    resulting_config = json.loads(config.read_text())
    assert resulting_config["oauthAccount"]["emailAddress"] == "second@example.com"
    assert resulting_config["projects"] == original_config["projects"]
    with original_open(transcript, "rb") as saved_transcript:
        assert saved_transcript.read() == transcript_bytes


def test_second_failure_does_not_reactivate_recently_exhausted_account(account_store):
    harness, observations = account_store
    hook = hook_module()
    assert hook.rotate_account(
        quota_failure(), switcher=harness.switcher, now=harness.clock.now
    )["status"] == "switched"
    observations["1"] = _usage(0)  # A lagging endpoint must not erase the failed-account cooldown.
    observations["2"] = _usage(100)
    switched_credentials = credentials(harness).read_bytes()
    result = hook.rotate_account(
        quota_failure(), switcher=harness.switcher, now=harness.clock.now + 1
    )
    assert result["status"] == "blocked"
    assert harness.active_number() == 2
    assert credentials(harness).read_bytes() == switched_credentials


@pytest.mark.parametrize("event", [
    quota_failure(error="authentication_failed"),
    quota_failure(agent_id="other-agent"),
    {"hook_event_name": "Stop", "error": "rate_limit"},
    quota_failure(),  # Ordinary 429 with usage below the actual quota.
])
def test_nonquota_events_leave_live_account_files_unchanged(account_store, event):
    harness, observations = account_store
    observations["1"] = _usage(35)
    paths = [credentials(harness), harness.temp_home / ".claude.json", harness.switcher.sequence_file]
    before = {path: path.read_bytes() for path in paths}
    result = hook_module().rotate_account(
        event, switcher=harness.switcher, now=harness.clock.now
    )
    assert result["status"] == "ignored"
    assert {path: path.read_bytes() for path in paths} == before


def test_mid_switch_failure_reports_blocked_and_rolls_back_credentials(account_store, monkeypatch):
    harness, _ = account_store
    config = harness.temp_home / ".claude.json"
    paths = [credentials(harness), config, harness.switcher.sequence_file]
    before = {path: path.read_bytes() for path in paths}
    original_write = harness.switcher._write_json

    def failed_config_write(path, value):
        if path == config:
            raise OSError("simulated config write failure")
        return original_write(path, value)

    monkeypatch.setattr(harness.switcher, "_write_json", failed_config_write)
    result = hook_module().rotate_account(
        quota_failure(), switcher=harness.switcher, now=harness.clock.now
    )
    assert result["status"] == "blocked"
    assert harness.active_number() == 1
    assert {path: path.read_bytes() for path in paths} == before


def test_cli_enable_refuses_single_saved_account_without_editing_settings(temp_home, monkeypatch):
    harness = EngineHarness(temp_home)
    harness.seed(1, "only@example.com")
    harness.make_live("only@example.com", 1)
    monkeypatch.setattr(
        harness.switcher, "_collect_usage_entries",
        lambda accounts_info, fetch=None: {"1": _entry_for(_usage(20), harness.clock.now)},
    )
    monkeypatch.setattr("claude_swap.switcher.ClaudeAccountSwitcher", lambda: harness.switcher)
    settings = temp_home / ".claude" / "settings.json"
    original = b'{ "model": "sonnet", "permissions": {"allow": ["Read"]} }\r\n'
    settings.write_bytes(original)

    assert hook_module().main(["enable"]) == 1
    assert settings.read_bytes() == original
    assert not list(settings.parent.glob("settings.json.sessioner-*.bak"))
    assert not hook_module().hook_enabled(settings)


def test_cli_status_reads_settings_without_constructing_account_engine(temp_home, monkeypatch, capsys):
    settings = temp_home / ".claude" / "settings.json"
    original = b'{ "model": "sonnet" }\r\n'
    settings.write_bytes(original)

    def forbidden_engine():
        pytest.fail("Reading hook status must not construct or mutate an account engine")

    monkeypatch.setattr("claude_swap.switcher.ClaudeAccountSwitcher", forbidden_engine)
    assert hook_module().main(["status"]) == 0
    assert "disabled" in capsys.readouterr().out
    assert settings.read_bytes() == original
    assert not list(settings.parent.glob("settings.json.sessioner-*.bak"))
