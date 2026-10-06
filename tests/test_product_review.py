"""Independent account-product regressions, with no real login or process I/O."""

from copy import deepcopy
import io
import sys

import pytest
from rich.console import Console

from sessioner.accounts import quota_hook
from sessioner.cli import main
from sessioner.service import SessionerService, Snapshot, usage_available


def saved(number, email, *, organization="", alias=None, usage=None, age=0):
    account = {
        "number": number,
        "email": email,
        "organizationUuid": organization,
        "alias": alias or f"login-{number}",
        "usageStatus": "ok" if usage is not None else "unavailable",
        "usage": usage,
        "usageAgeSeconds": age,
    }
    return account


class AccountBoundary:
    """Model identities by email AND organization, independently of display names."""

    def __init__(self, accounts, live):
        self.accounts = deepcopy(accounts)
        self.live = live
        self.calls = []

    def list_accounts(self, *, json_output, fetch=None):
        assert json_output is True
        self.calls.append(("list", fetch))
        active = next((account["number"] for account in self.accounts
                       if (account["email"], account["organizationUuid"]) == self.live), None)
        return {"schemaVersion": 1, "activeAccountNumber": active,
                "accounts": deepcopy(self.accounts)}

    def status(self, *, json_output):
        assert json_output is True
        return {"schemaVersion": 1,
                "active": {"email": self.live[0], "managed": False} if self.live else None}

    def add_account(self, *, slot, alias):
        assert slot is None
        self.calls.append(("add", self.live, alias))
        account = next((account for account in self.accounts
                        if (account["email"], account["organizationUuid"]) == self.live), None)
        if account is None:
            number = max((account["number"] for account in self.accounts), default=0) + 1
            self.accounts.append(saved(number, self.live[0], organization=self.live[1], alias=alias))
        else:
            account["alias"] = alias

    def switch_to(self, identifier, *, json_output):
        assert json_output is True
        account = next(account for account in self.accounts if str(account["number"]) == identifier)
        identity = (account["email"], account["organizationUuid"])
        changed = identity != self.live
        self.live = identity
        self.calls.append(("switch", identifier))
        return {"schemaVersion": 1, "switched": changed,
                "to": {"number": account["number"], "email": account["email"]}}


class Terminal(io.StringIO):
    def isatty(self):
        return True


def product(tmp_path, engine):
    return SessionerService(switcher=engine, settings_path=tmp_path / "settings.json",
                            which=lambda command: "claude.exe")


def run(monkeypatch, arguments, service, reader, *, interactive=True):
    monkeypatch.setattr(sys, "stdin", Terminal() if interactive else io.StringIO())
    output = io.StringIO()
    code = main(arguments, service=service,
                console=Console(file=output, force_terminal=False, width=120), input_fn=reader)
    return code, output.getvalue()


@pytest.mark.parametrize("changes,expected", [
    ({"usageAgeSeconds": 0}, True),
    ({"usageAgeSeconds": 300}, True),
    ({"usageAgeSeconds": 300.01}, False),
    ({"usageAgeSeconds": -1}, False),
    ({"usageAgeSeconds": True}, False),
    ({"usageAgeSeconds": float("nan")}, False),
    ({"usageAgeSeconds": float("inf")}, False),
    ({"usageStatus": "unavailable"}, False),
    ({"disabled": True}, False),
    ({"usage": {}}, False),
    ({"usage": {"fiveHour": {"pct": 100}}}, False),
    ({"usage": {"fiveHour": {"pct": -1}}}, False),
    ({"usage": {"fiveHour": {"pct": True}}}, False),
    ({"usage": {"fiveHour": {"pct": float("nan")}}}, False),
    ({"usage": {"fiveHour": {"pct": 1}, "scoped": {}}}, False),
    ({"usage": {"fiveHour": {"pct": 1}, "scoped": [{"pct": 100}]}}, False),
    ({"usage": {"fiveHour": {"pct": 1}, "scoped": [{"pct": 99.9}]}}, True),
])
def test_product_backup_readiness_agrees_with_actual_hook_contract(changes, expected):
    active = saved(1, "active@example.com", usage={"fiveHour": {"pct": 100}})
    backup = saved(2, "backup@example.com", usage={"fiveHour": {"pct": 20}, "sevenDay": {"pct": 10}})
    backup.update(changes)
    state = Snapshot([active, backup], active, active["email"], True, False, None, "claude.exe")
    assert usage_available(backup) is expected
    assert state.backup_available is expected
    event = {"hook_event_name": "StopFailure", "error": "rate_limit"}
    roster = {"schemaVersion": 1, "activeAccountNumber": 1, "accounts": [active, backup]}
    if expected:
        assert quota_hook.choose_account(event, roster) == 2
    else:
        with pytest.raises(ValueError, match="fresh available usage"):
            quota_hook.choose_account(event, roster)


@pytest.mark.parametrize("interruption", ["q", EOFError, KeyboardInterrupt])
def test_cancel_after_external_login_keeps_unsaved_live_identity(tmp_path, monkeypatch, interruption):
    engine = AccountBoundary([], ("original@example.com", ""))
    service = product(tmp_path, engine)
    steps = 0

    def reader(prompt):
        nonlocal steps
        steps += 1
        if steps == 1:
            return ""
        engine.live = ("unsaved@example.com", "new-org")
        if isinstance(interruption, str):
            return interruption
        raise interruption

    code, text = run(monkeypatch, ["setup"], service, reader)
    assert code == 0 and "paused" in text.lower()
    assert engine.live == ("unsaved@example.com", "new-org")
    assert len(engine.accounts) == 1
    assert engine.accounts[0]["email"] == "original@example.com"
    assert not any(call[0] == "switch" for call in engine.calls)
    assert not service.settings_path.exists()


def test_existing_accounts_do_not_hide_an_unsaved_live_login(tmp_path, monkeypatch):
    engine = AccountBoundary([
        saved(2, "first@example.com", alias="primary"),
        saved(9, "second@example.com", alias="backup"),
    ], ("unmanaged@example.com", ""))
    service = product(tmp_path, engine)

    def no_prompt(prompt):
        pytest.fail("Status cannot prompt")

    code, text = run(monkeypatch, ["status"], service, no_prompt, interactive=False)
    assert code == 0 and "unmanaged@example.com (not saved)" in text
    assert "sessioner add" in text
    assert engine.calls == [("list", set())]
    assert service.snapshot().enable_problem() is not None


def test_same_email_in_different_organizations_keeps_selected_slot(tmp_path, monkeypatch):
    engine = AccountBoundary([
        saved(2, "shared@example.com", organization="org-a", alias="work"),
        saved(9, "shared@example.com", organization="org-b", alias="backup"),
    ], ("shared@example.com", "org-a"))
    service = product(tmp_path, engine)

    def no_prompt(prompt):
        pytest.fail("An explicit switch cannot prompt")

    code, text = run(monkeypatch, ["switch", "BACKUP"], service, no_prompt, interactive=False)
    assert code == 0 and "Switched" in text
    assert engine.live == ("shared@example.com", "org-b")
    state = service.snapshot()
    assert state.active["number"] == 9 and len(state.enabled) == 2


def test_status_names_user_settings_scope_and_live_confirmation(tmp_path, monkeypatch):
    engine = AccountBoundary([
        saved(1, "primary@example.com", usage={"fiveHour": {"pct": 10}}),
        saved(2, "backup@example.com", usage={"fiveHour": {"pct": 20}}),
    ], ("primary@example.com", ""))
    service = product(tmp_path, engine)
    quota_hook.configure_hook(service.settings_path, True)
    code, text = run(monkeypatch, ["status"], service, lambda prompt: "q", interactive=False)
    assert code == 0 and "On in user settings" in text
    assert "/hooks" in text and "project" in text and "managed" in text
    assert engine.calls == [("list", set())]


def test_hook_installation_cannot_hide_user_hook_disablement(tmp_path, monkeypatch):
    engine = AccountBoundary([
        saved(1, "primary@example.com"), saved(2, "backup@example.com"),
    ], ("primary@example.com", ""))
    service = product(tmp_path, engine)
    service.settings_path.write_text('{"disableAllHooks": true}', encoding="utf-8")
    quota_hook.configure_hook(service.settings_path, True)
    original = service.settings_path.read_bytes()
    code, text = run(monkeypatch, ["status"], service, lambda prompt: "q", interactive=False)
    assert code == 0 and "Blocked" in text
    assert "On in user settings" not in text
    code, text = run(monkeypatch, ["on"], service, lambda prompt: "q", interactive=False)
    assert code == 1 and "disableAllHooks" in text
    assert service.settings_path.read_bytes() == original


def test_managed_only_setting_is_actionable_and_cannot_enable_user_hook(tmp_path, monkeypatch):
    engine = AccountBoundary([
        saved(1, "primary@example.com"), saved(2, "backup@example.com"),
    ], ("primary@example.com", ""))
    service = product(tmp_path, engine)
    service.settings_path.write_text('{"allowManagedHooksOnly": true}', encoding="utf-8")
    original = service.settings_path.read_bytes()
    code, text = run(monkeypatch, ["doctor"], service, lambda prompt: "q", interactive=False)
    assert code == 1 and "managed" in text and str(service.settings_path) in text
    code, text = run(monkeypatch, ["on"], service, lambda prompt: "q", interactive=False)
    assert code == 1 and "managed" in text
    assert service.settings_path.read_bytes() == original


def duplicate_active_identity_roster():
    duplicate = saved(1, "active@example.com", alias="old-primary", usage={"fiveHour": {"pct": 20}})
    active = saved(4, "active@example.com", alias="primary", usage={"fiveHour": {"pct": 100}})
    exhausted_backup = saved(8, "other@example.com", alias="backup", usage={"fiveHour": {"pct": 100}})
    return active, [duplicate, active, exhausted_backup]


def test_duplicate_slot_of_active_identity_is_not_a_ready_backup():
    active, accounts = duplicate_active_identity_roster()
    state = Snapshot(accounts, active, active["email"], True, False, None, "claude.exe")
    assert len(state.enabled) == 2
    assert state.backup_available is False


def test_hook_cannot_rotate_to_another_slot_for_the_same_exhausted_identity():
    active, accounts = duplicate_active_identity_roster()
    roster = {"schemaVersion": 1, "activeAccountNumber": active["number"], "accounts": accounts}
    event = {"hook_event_name": "StopFailure", "error": "rate_limit"}
    with pytest.raises(ValueError, match="fresh available usage"):
        quota_hook.choose_account(event, roster)


def test_a_fresh_duplicate_of_a_different_backup_identity_remains_usable():
    active = saved(1, "active@example.com", usage={"fiveHour": {"pct": 100}})
    older_backup = saved(3, "backup@example.com", alias="old-backup", usage={"fiveHour": {"pct": 100}})
    fresh_backup = saved(9, "backup@example.com", alias="backup", usage={"fiveHour": {"pct": 20}})
    accounts = [active, older_backup, fresh_backup]
    roster = {"schemaVersion": 1, "activeAccountNumber": 1, "accounts": accounts}
    event = {"hook_event_name": "StopFailure", "error": "rate_limit"}
    assert quota_hook.choose_account(event, roster) == 9
    state = Snapshot(accounts, active, active["email"], True, False, None, "claude.exe")
    assert len(state.enabled) == 2
    assert state.backup_available is True
