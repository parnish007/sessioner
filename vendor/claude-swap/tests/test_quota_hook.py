"""Account rotation and hook installation; Claude owns the conversation."""

import importlib
import json
import sys

import pytest


def module():
    try:
        return importlib.import_module("claude_swap.quota_hook")
    except ModuleNotFoundError:
        pytest.fail("The account-only quota hook is not implemented")


def roster(first=100, second=20):
    return {
        "schemaVersion": 1, "activeAccountNumber": 1,
        "accounts": [
            {"number": number, "email": f"account{number}@example.com", "organizationUuid": "",
             "usageStatus": "ok", "usageAgeSeconds": 0,
             "usage": {"fiveHour": {"pct": pct}, "sevenDay": {"pct": 0}}}
            for number, pct in [(1, first), (2, second)]
        ],
    }


def failure(**extra):
    return {"hook_event_name": "StopFailure", "error": "rate_limit", **extra}


def test_exhaustion_selects_other_account():
    assert module().choose_account(failure(), roster()) == 2


def test_duplicate_active_login_is_not_a_different_backup():
    data = roster()
    data["accounts"][1]["email"] = "ACCOUNT1@example.com"
    with pytest.raises(ValueError, match="available usage"):
        module().choose_account(failure(), data)


def test_duplicate_of_recently_failed_login_is_excluded_too():
    data = roster(20, 100)
    data["activeAccountNumber"] = 2
    data["accounts"].append({**data["accounts"][0], "number": 3, "email": "ACCOUNT1@example.com"})
    with pytest.raises(ValueError, match="available usage"):
        module().choose_account(failure(), data, excluded={1})


def test_distinct_organization_remains_an_eligible_identity():
    data = roster()
    data["accounts"][1]["email"] = "account1@example.com"
    data["accounts"][1]["organizationUuid"] = "another-organization"
    assert module().choose_account(failure(), data) == 2


def test_ordinary_rate_throttle_keeps_account():
    assert module().choose_account(failure(), roster(35)) is None


def test_explicit_quota_message_works_with_lagging_active_usage():
    assert module().choose_account(failure(last_assistant_message="You've hit your limit"), roster(98)) == 2


@pytest.mark.parametrize("extra", [{"error": "authentication_failed"}, {"agent_id": "subagent"}, {"hook_event_name": "Stop"}])
def test_unrelated_failures_do_not_rotate(extra):
    assert module().choose_account(failure(**extra), roster()) is None


@pytest.mark.parametrize("changes", [{"usageAgeSeconds": 900}, {"usageStatus": "unavailable"}, {"disabled": True}, {"usageAgeSeconds": -1}])
def test_unusable_backup_blocks_rotation(changes):
    data = roster()
    data["accounts"][1].update(changes)
    with pytest.raises(ValueError, match="available usage"):
        module().choose_account(failure(), data)


def test_two_exhausted_accounts_block_rotation():
    with pytest.raises(ValueError, match="available usage"):
        module().choose_account(failure(), roster(100, 100))


def test_recently_failed_account_is_not_selected_again():
    data = roster(98, 20)
    data["activeAccountNumber"] = 2
    with pytest.raises(ValueError, match="available usage"):
        module().choose_account(failure(last_assistant_message="You've hit your limit"), data, excluded={1})


def test_scoped_backup_limit_is_respected():
    data = roster()
    data["accounts"][1]["usage"]["scoped"] = [{"name": "Sonnet", "pct": 100}]
    with pytest.raises(ValueError, match="available usage"):
        module().choose_account(failure(), data)


def test_settings_install_preserves_existing_settings_and_is_idempotent(tmp_path):
    settings = tmp_path / "settings.json"
    original = {"permissions": {"allow": ["Read"]}, "hooks": {"StopFailure": [{"matcher": "server_error", "hooks": [{"type": "command", "command": "existing-hook"}]}]}}
    settings.write_text(json.dumps(original), encoding="utf-8")
    hook = module()
    hook.configure_hook(settings, True)
    hook.configure_hook(settings, True)
    result = json.loads(settings.read_text(encoding="utf-8"))
    assert result["permissions"] == original["permissions"]
    assert result["hooks"]["StopFailure"][0] == original["hooks"]["StopFailure"][0]
    assert len(result["hooks"]["StopFailure"]) == 2
    assert set(result["hooks"]) == {"StopFailure"}
    installed = result["hooks"]["StopFailure"][1]
    assert installed["matcher"] == "rate_limit"
    assert installed["hooks"][0]["command"] == sys.executable
    assert installed["hooks"][0]["args"] == ["-m", "claude_swap.quota_hook", "run"]
    hook.configure_hook(settings, False)
    assert json.loads(settings.read_text(encoding="utf-8")) == original


def test_invalid_settings_are_never_overwritten(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError):
        module().configure_hook(settings, True)
    assert settings.read_text(encoding="utf-8") == "{broken"


def test_settings_backup_retains_original_bytes(tmp_path):
    settings = tmp_path / "settings.json"
    original = b'{ "model": "sonnet" }\r\n'
    settings.write_bytes(original)
    module().configure_hook(settings, True)
    backups = list(tmp_path.glob("settings.json.sessioner-*.bak"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original


@pytest.mark.parametrize("changes", [{"usageAgeSeconds": 900}, {"usageStatus": "unavailable"}])
def test_stale_active_usage_does_not_turn_a_throttle_into_quota_exhaustion(changes):
    data = roster()
    data["accounts"][0].update(changes)
    assert module().choose_account(failure(), data) is None
    assert module().choose_account(failure(error_details="Quota exhausted"), data) == 2


def test_disabling_uninstalled_hook_preserves_empty_hook_settings(tmp_path):
    settings = tmp_path / "settings.json"
    original = b'{ "hooks": {}, "model": "sonnet" }\r\n'
    settings.write_bytes(original)
    module().configure_hook(settings, False)
    assert settings.read_bytes() == original
    assert not list(tmp_path.glob("settings.json.sessioner-*.bak"))
