"""Exercise real Sessioner policy and UI with an external-engine double."""

from copy import deepcopy
import importlib
import io
import json
import sys

import pytest
from rich.console import Console


def product_module(name):
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("sessioner"):
            pytest.fail("The Sessioner product interface is not implemented yet")
        raise


def row(number, email, name=None, *, disabled=False, usage=None):
    result = {
        "number": number,
        "email": email,
        "organizationName": "",
        "organizationUuid": "",
        "isOrganization": False,
        "active": False,
        "usageStatus": "ok" if usage else "unavailable",
        "usage": usage,
        "usageAgeSeconds": 10 if usage else None,
    }
    if name:
        result["alias"] = name
    if disabled:
        result["disabled"] = True
    return result


class Engine:
    """Only the inherited account boundary is replaced; hook writes are real."""

    def __init__(self, accounts=(), login=None):
        self.rows = deepcopy(list(accounts))
        self.login = login
        self.calls = []

    def list_accounts(self, *, json_output, fetch=None, force=False):
        assert json_output is True
        self.calls.append(("list", fetch))
        if force:
            self.calls.append(("forced",))
        rows = deepcopy(self.rows)
        active = None
        for account in rows:
            account["active"] = account["email"] == self.login
            if account["active"]:
                active = account["number"]
        return {"schemaVersion": 1, "activeAccountNumber": active, "accounts": rows}

    def status(self, *, json_output):
        assert json_output is True
        # A saved account's status invokes remote quota work in the real engine.
        assert not any(account["email"] == self.login for account in self.rows)
        self.calls.append(("status",))
        active = {"email": self.login, "managed": False} if self.login else None
        return {"schemaVersion": 1, "active": active}

    def add_account(self, *, slot, alias):
        assert slot is None
        assert self.login
        self.calls.append(("add", slot, alias))
        prior = next((account for account in self.rows if account["email"] == self.login), None)
        if prior:
            prior["alias"] = alias
        else:
            number = max((account["number"] for account in self.rows), default=0) + 1
            self.rows.append(row(number, self.login, alias))
        print("Added Account (inherited implementation output)")

    def switch_to(self, identifier, *, json_output):
        assert json_output is True
        target = next(account for account in self.rows if str(account["number"]) == identifier)
        old_login = self.login
        self.login = target["email"]
        self.calls.append(("switch", identifier))
        return {
            "schemaVersion": 1, "switched": old_login != self.login,
            "strategy": "direct", "reason": "switched" if old_login != self.login else "already-active",
            "from": {"number": None, "email": old_login},
            "to": {"number": target["number"], "email": self.login},
            "warnings": [],
        }


class TTY(io.StringIO):
    def isatty(self):
        return True


def service(tmp_path, engine, *, claude=True):
    module = product_module("sessioner.service")
    return module.SessionerService(
        switcher=engine, settings_path=tmp_path / "profile" / ".claude" / "settings.json",
        which=lambda command: "claude.exe" if claude else None,
    )


def invoke(monkeypatch, argv, service=None, answers=(), *, interactive=False, reader=None):
    output = io.StringIO()
    console = Console(file=output, force_terminal=False, width=110)
    monkeypatch.setattr(sys, "stdin", TTY() if interactive else io.StringIO())
    iterator = iter(answers)

    def read(prompt):
        if reader:
            return reader(prompt)
        try:
            return next(iterator)
        except StopIteration:
            raise EOFError

    code = product_module("sessioner.cli").main(argv, service=service, console=console, input_fn=read)
    return code, output.getvalue()


def test_help_and_version_work_without_an_account_store(monkeypatch, capsys):
    # Catch eager backend construction during public informational commands.
    code, text = invoke(monkeypatch, ["--help"])
    assert code == 0
    assert all(command in text for command in ("setup", "accounts", "switch", "doctor"))
    code, text = invoke(monkeypatch, ["--version"])
    assert code == 0
    assert "Sessioner 0.1.0" in text
    # The real entry point must print too, without an injected Console.
    assert product_module("sessioner.cli").main(["--help"]) == 0
    assert "sessioner" in capsys.readouterr().out


def test_no_arguments_in_a_pipe_returns_status_without_waiting(tmp_path, monkeypatch):
    engine = Engine()

    def cannot_prompt(prompt):
        pytest.fail("Piped status must never wait for input")

    code, text = invoke(monkeypatch, [], service(tmp_path, engine), reader=cannot_prompt)
    assert code == 0
    assert "Sessioner" in text and "0 saved" in text
    assert "sessioner setup" in text
    assert engine.rows == []


def test_launcher_invocation_is_used_in_next_steps_help_and_setup(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSIONER_COMMAND", ".\\sessioner.ps1")
    product = service(tmp_path, Engine())
    code, text = invoke(monkeypatch, [], product)
    assert code == 0 and "Next: .\\sessioner.ps1 setup" in text
    code, text = invoke(monkeypatch, ["--help"])
    assert code == 0 and "usage: .\\sessioner.ps1" in text
    product.switcher.login = "first@example.com"
    code, text = invoke(monkeypatch, ["setup"], product, ["q"], interactive=True)
    assert code == 0 and "Next: .\\sessioner.ps1 setup" in text
    assert "Sessioner setup" in text


def test_status_uses_cached_quota_and_does_not_claim_unknown_backup_is_ready(tmp_path, monkeypatch):
    engine = Engine([row(1, "first@example.com", "primary"), row(2, "second@example.com", "backup")], "first@example.com")
    code, text = invoke(monkeypatch, ["status"], service(tmp_path, engine))
    assert code == 0 and "primary" in text and "first@example.com" in text
    assert "unknown" in text.lower() or "unavailable" in text.lower()
    assert "sessioner accounts" in text
    assert all(call != ("list", None) for call in engine.calls)


def test_unsaved_active_login_is_not_mislabeled_as_unknown_saved_quota(tmp_path, monkeypatch):
    usage = {"fiveHour": {"pct": 20}, "sevenDay": {"pct": 30}}
    engine = Engine([
        row(1, "first@example.com", "primary", usage=usage),
        row(2, "second@example.com", "backup", usage=usage),
    ], "unsaved@example.com")
    product = service(tmp_path, engine)
    for command in ("status", "doctor"):
        code, text = invoke(monkeypatch, [command], product)
        assert code == (1 if command == "doctor" else 0)
        assert "sessioner add" in text and "unsaved@example.com" in text
        assert "Backup usage is unknown" not in text


def test_accounts_shows_names_usage_and_disabled_state(tmp_path, monkeypatch):
    engine = Engine([
        row(1, "first@example.com", "primary", usage={"fiveHour": {"pct": 83}, "sevenDay": {"pct": 41}}),
        row(2, "second@example.com", "backup", disabled=True),
    ], "first@example.com")
    code, text = invoke(monkeypatch, ["accounts"], service(tmp_path, engine))
    assert code == 0
    assert "primary" in text and "first@example.com" in text and "active" in text.lower()
    assert "83%" in text and "41%" in text and "disabled" in text.lower()
    assert "unknown" in text.lower() or "unavailable" in text.lower()
    assert ("list", None) in engine.calls


def test_add_assigns_names_without_displacing_a_saved_account(tmp_path, monkeypatch, capsys):
    engine = Engine(login="first@example.com")
    product = service(tmp_path, engine)
    code, text = invoke(monkeypatch, ["add"], product)
    assert code == 0 and engine.rows[0]["alias"] == "primary"
    original = deepcopy(engine.rows[0])
    engine.login = "second@example.com"
    code, text = invoke(monkeypatch, ["add"], product)
    assert code == 0 and len(engine.rows) == 2
    assert engine.rows[0] == original and engine.rows[1]["alias"] == "backup"
    assert "Added Account" not in capsys.readouterr().out


@pytest.mark.parametrize("name", ["primary", "work"])
def test_add_same_login_refreshes_existing_name_without_inventing_a_backup(tmp_path, monkeypatch, name):
    engine = Engine([row(1, "first@example.com", name)], "first@example.com")
    code, text = invoke(monkeypatch, ["add"], service(tmp_path, engine))
    assert code == 0 and len(engine.rows) == 1
    assert engine.rows[0]["alias"] == name and "updated" in text.lower()


def test_add_rejects_a_duplicate_name_before_capturing_login(tmp_path, monkeypatch):
    engine = Engine([row(1, "first@example.com", "work")], "second@example.com")
    code, text = invoke(monkeypatch, ["add", "WORK"], service(tmp_path, engine))
    assert code == 1 and len(engine.rows) == 1
    assert "name" in text.lower() and "already" in text.lower()
    assert "sessioner add" in text


def test_switch_by_name_changes_only_the_selected_account(tmp_path, monkeypatch):
    engine = Engine([row(1, "first@example.com", "primary"), row(5, "second@example.com", "backup")], "first@example.com")
    code, text = invoke(monkeypatch, ["switch", "BACKUP"], service(tmp_path, engine))
    assert code == 0 and engine.login == "second@example.com"
    assert "backup" in text and "Switched" in text
    assert ("switch", "5") in engine.calls


def test_switch_without_a_target_in_a_pipe_gives_an_actionable_error(tmp_path, monkeypatch):
    engine = Engine([row(1, "first@example.com", "primary")], "first@example.com")
    code, text = invoke(monkeypatch, ["switch"], service(tmp_path, engine))
    assert code == 1 and "sessioner switch" in text
    assert not any(call[0] == "switch" for call in engine.calls)


def test_empty_switch_target_cannot_select_an_account_without_a_name(tmp_path, monkeypatch):
    engine = Engine([row(1, "first@example.com"), row(2, "second@example.com")], "second@example.com")
    code, text = invoke(monkeypatch, ["switch", ""], service(tmp_path, engine))
    assert code == 1 and "sessioner switch" in text
    assert engine.login == "second@example.com"


def test_switch_offers_real_account_numbers_in_a_terminal(tmp_path, monkeypatch):
    engine = Engine([row(2, "first@example.com", "work"), row(8, "second@example.com", "backup")], "first@example.com")
    code, text = invoke(monkeypatch, ["switch"], service(tmp_path, engine), ["8"], interactive=True)
    assert code == 0 and engine.login == "second@example.com"
    assert "8" in text and "backup" in text


def test_on_and_off_preserve_unrelated_settings_and_only_change_the_product_hook(tmp_path, monkeypatch):
    engine = Engine([row(1, "first@example.com", "primary"), row(2, "second@example.com", "backup")], "first@example.com")
    product = service(tmp_path, engine)
    product.settings_path.parent.mkdir(parents=True, exist_ok=True)
    original = {"theme": "light", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "keep-me"}]}]}}
    product.settings_path.write_text(json.dumps(original), encoding="utf-8")
    code, text = invoke(monkeypatch, ["enable"], product)
    assert code == 0 and "StopFailure" in product.settings_path.read_text()
    assert "/hooks" in text and "rate_limit" in text
    assert json.loads(product.settings_path.read_text())["hooks"]["Stop"] == original["hooks"]["Stop"]
    code, text = invoke(monkeypatch, ["disable"], product)
    assert code == 0 and json.loads(product.settings_path.read_text()) == original


@pytest.mark.parametrize("condition", ["one_account", "disabled_backup", "unsaved_active", "all_hooks_disabled"])
def test_on_refuses_an_ineffective_hook(tmp_path, monkeypatch, condition):
    accounts = [row(1, "first@example.com", "primary"), row(2, "second@example.com", "backup")]
    login = "first@example.com"
    if condition == "one_account":
        accounts.pop()
    if condition == "disabled_backup":
        accounts[1]["disabled"] = True
    if condition == "unsaved_active":
        login = "unsaved@example.com"
    product = service(tmp_path, Engine(accounts, login))
    if condition == "all_hooks_disabled":
        product.settings_path.parent.mkdir(parents=True, exist_ok=True)
        product.settings_path.write_text('{"disableAllHooks": true}', encoding="utf-8")
    before = product.settings_path.read_bytes() if product.settings_path.exists() else None
    code, text = invoke(monkeypatch, ["on"], product)
    assert code == 1 and "Sessioner" in text
    assert "Next:" in text or "Fix:" in text
    assert (product.settings_path.read_bytes() if product.settings_path.exists() else None) == before


def test_doctor_explains_missing_claude_login_and_malformed_settings(tmp_path, monkeypatch):
    product = service(tmp_path, Engine(), claude=False)
    product.settings_path.parent.mkdir(parents=True, exist_ok=True)
    product.settings_path.write_text("not-json", encoding="utf-8")
    code, text = invoke(monkeypatch, ["doctor"], product)
    assert code == 1 and "Claude Code" in text and "/login" in text
    assert "settings.json" in text and "sessioner setup" in text
    assert product.settings_path.read_text() == "not-json"


def test_setup_saves_two_distinct_logins_restores_starting_account_and_enables(tmp_path, monkeypatch):
    engine = Engine(login="first@example.com")
    product = service(tmp_path, engine)
    reads = iter(["", "", "", "", ""])
    number = 0

    def reader(prompt):
        nonlocal number
        number += 1
        if number == 2:
            engine.login = "second@example.com"
        return next(reads)

    code, text = invoke(monkeypatch, ["setup"], product, interactive=True, reader=reader)
    assert code == 0 and len(engine.rows) == 2
    assert {account["email"] for account in engine.rows} == {"first@example.com", "second@example.com"}
    assert engine.login == "first@example.com" and product.settings_path.exists()
    assert "/login" in text and "/hooks" in text and "StopFailure" in text
    assert "claude.exe" not in text


def test_setup_rechecks_same_login_and_cancels_without_enabling(tmp_path, monkeypatch):
    engine = Engine(login="first@example.com")
    product = service(tmp_path, engine)
    code, text = invoke(monkeypatch, ["setup"], product, ["", "", "q"], interactive=True)
    assert code == 0 and len(engine.rows) == 1
    assert "same" in text.lower() and "sessioner setup" in text
    assert not product.settings_path.exists()


@pytest.mark.parametrize("interruption", [EOFError, KeyboardInterrupt])
def test_setup_cancels_cleanly_before_the_first_account_write(tmp_path, monkeypatch, interruption):
    engine = Engine(login="first@example.com")
    product = service(tmp_path, engine)

    def reader(prompt):
        raise interruption

    code, text = invoke(monkeypatch, ["setup"], product, interactive=True, reader=reader)
    assert code == 0 and engine.rows == [] and not product.settings_path.exists()
    assert "Sessioner" in text and "sessioner setup" in text
