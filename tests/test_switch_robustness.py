"""Switching has to survive the awkward cases: a bad first choice, a held lock, damaged state."""

import json

import pytest

from sessioner.accounts import quota_hook
from sessioner.accounts.exceptions import LockError
from test_cli import Engine, product_module, row, service
from test_watcher import NOW, usage

EVENT = {"hook_event_name": "StopFailure", "error": "rate_limit", "error_details": "You've hit your usage limit"}


def hook_row(number, pct):
    return {"number": number, "email": f"u{number}@example.com", "usageStatus": "ok", "usageAgeSeconds": 1,
            "usage": {"fiveHour": {"pct": pct}, "sevenDay": {"pct": pct}}}


class HookEngine:
    """The account boundary as the hook sees it; `bad` slots refuse to become active."""

    def __init__(self, backup_dir, rows, *, bad=(), how="raise"):
        self.backup_dir = backup_dir
        self.rows = rows
        self.active = 1
        self.bad = set(bad)
        self.how = how
        self.calls = []

    def list_accounts(self, *, json_output):
        return {"schemaVersion": 1, "activeAccountNumber": self.active, "accounts": self.rows}

    def switch_to(self, identifier, *, json_output):
        self.calls.append(identifier)
        if int(identifier) in self.bad:
            if self.how == "raise":
                raise RuntimeError("credential for this slot cannot be read")
            return {"schemaVersion": 1, "switched": False, "to": {"number": int(identifier)}}
        self.active = int(identifier)
        return {"schemaVersion": 1, "switched": True, "to": {"number": int(identifier)}}

    def status(self, *, json_output):
        return {"schemaVersion": 1, "active": {"number": self.active, "managed": True}}


def three(tmp_path, **kwargs):
    # 1 is active and spent; 2 has the most room so it is tried first; 3 is the fallback.
    return HookEngine(tmp_path, [hook_row(1, 100), hook_row(2, 20), hook_row(3, 50)], **kwargs)


# ---- the hook ----

@pytest.mark.parametrize("how", ["raise", "unverified"])
def test_hook_falls_back_to_the_next_candidate_when_the_first_cannot_be_switched_to(tmp_path, how):
    engine = three(tmp_path, bad={2}, how=how)
    result = quota_hook.rotate_account(EVENT, engine, now=NOW.timestamp())
    assert result == {"status": "switched", "from": 1, "to": 3}
    assert engine.calls == ["2", "3"]
    # The slot that failed is remembered, so the next limit does not walk straight back into it.
    cooldowns = json.loads((tmp_path / "sessioner-hook-state.json").read_text())
    assert {"1", "2"} <= set(cooldowns)


def test_hook_reports_blocked_and_leaves_the_login_alone_when_every_candidate_fails(tmp_path):
    engine = three(tmp_path, bad={2, 3})
    assert quota_hook.rotate_account(EVENT, engine, now=NOW.timestamp()) == {"status": "blocked"}
    assert engine.active == 1
    assert engine.calls == ["2", "3"]  # each candidate once, no loop


@pytest.mark.parametrize("damage", ["{not json", "[]", '{"x": "y"}', '{"2": "soon"}', ""])
def test_a_damaged_cooldown_file_is_reset_instead_of_blocking_every_future_switch(tmp_path, damage):
    state = tmp_path / "sessioner-hook-state.json"
    state.write_text(damage)
    engine = three(tmp_path)
    assert quota_hook.rotate_account(EVENT, engine, now=NOW.timestamp())["status"] == "switched"
    assert isinstance(json.loads(state.read_text()), dict)  # healed, and valid for the next run


# ---- switching by hand (browser, CLI) ----

class Contended(Engine):
    """Another account operation (the hook, another window) holds the lock for a few tries."""

    def __init__(self, *args, held=1, **kwargs):
        super().__init__(*args, **kwargs)
        self.held = held

    def switch_to(self, identifier, *, json_output):
        if self.held:
            self.held -= 1
            self.calls.append(("locked", identifier))
            raise LockError("another operation holds the account lock")
        return super().switch_to(identifier, json_output=json_output)


def two_accounts():
    return [row(1, "a@example.com", "work", usage=usage(10)), row(2, "b@example.com", "home", usage=usage(10))]


def test_a_manual_switch_waits_out_a_briefly_held_lock(tmp_path):
    engine = Contended(two_accounts(), login="a@example.com", held=2)
    svc = service(tmp_path, engine)
    waits = []
    svc.sleep = waits.append
    account, changed = svc.switch("home")
    assert changed and account["alias"] == "home"
    assert len(waits) == 2 and all(0 < wait <= 2 for wait in waits)


def test_a_manual_switch_gives_up_politely_when_the_lock_never_frees(tmp_path):
    engine = Contended(two_accounts(), login="a@example.com", held=99)
    svc = service(tmp_path, engine)
    svc.sleep = lambda seconds: None
    with pytest.raises(product_module("sessioner.service").SessionerError, match="in progress"):
        svc.switch("home")
    assert engine.login == "a@example.com"
    assert len([call for call in engine.calls if call[0] == "locked"]) <= 4  # bounded, never a spin


# ---- the watcher ----

class Flaky(Engine):
    def __init__(self, *args, bad=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.bad = set(bad)

    def switch_to(self, identifier, *, json_output):
        if identifier in self.bad:
            self.calls.append(("failed", identifier))
            raise RuntimeError("credential for this slot cannot be read")
        return super().switch_to(identifier, json_output=json_output)


def test_watcher_falls_back_to_the_next_candidate_when_the_first_cannot_be_switched_to(tmp_path):
    from sessioner.watcher import ResetWatcher

    spent = {"fiveHour": {"pct": 100}, "sevenDay": {"pct": 100}}
    engine = Flaky([
        row(1, "active@example.com", "work", usage=spent),
        row(2, "b@example.com", "mid", usage=usage(50)),
        row(3, "c@example.com", "roomy", usage=usage(20)),
    ], login="active@example.com", bad={"3"})
    watcher = ResetWatcher(service(tmp_path, engine), state_path=tmp_path / "w.json", clock=lambda: NOW.timestamp())
    watcher.set_enabled(True)
    result = watcher.tick()
    assert result["state"] == "switched" and result["lastSwitchTo"] == 2
    assert ("failed", "3") in engine.calls and ("switch", "2") in engine.calls
