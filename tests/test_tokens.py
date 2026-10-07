"""Token usage comes from Claude's local counters, split by the login that was active at the time."""

import json

import pytest

from sessioner.accounts import history
from sessioner.tokens import TokenLedger
from test_cli import Engine, invoke, product_module, row, service

T0 = 1_780_000_000.0  # an arbitrary moment; everything else is relative to it


def stamp(offset):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(T0 + offset, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def reply(session, offset, *, model="claude-opus-5-5", message_id=None, tokens=(10, 20, 30, 40), cwd="D:\\work\\parser", text="SECRET PROMPT TEXT"):
    return {
        "type": "assistant", "sessionId": session, "timestamp": stamp(offset), "cwd": cwd,
        "requestId": f"req-{message_id or offset}",
        "message": {"id": message_id or f"msg-{session}-{offset}", "model": model, "content": [{"type": "text", "text": text}],
                    "usage": {"input_tokens": tokens[0], "output_tokens": tokens[1], "cache_creation_input_tokens": tokens[2], "cache_read_input_tokens": tokens[3]}},
    }


def write(path, *items, partial=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item) + "\n")
        if partial is not None:
            handle.write(json.dumps(partial)[:-5])


def accounts():
    return [row(1, "a@example.com", "work"), row(2, "b@example.com", "home")]


def test_counts_each_reply_once_per_session_and_model(tmp_path):
    projects = tmp_path / "projects"
    write(projects / "p" / "s1.jsonl",
          {"type": "user", "sessionId": "s1", "message": {"content": "hello"}},
          reply("s1", 10),
          reply("s1", 20, model="claude-haiku-4-5-20251001", tokens=(1, 2, 0, 0)),
          reply("s1", 30, message_id="dup", tokens=(5, 1, 0, 0)),
          reply("s1", 30, message_id="dup", tokens=(5, 9, 0, 0)),  # the same reply, written again as it streamed
          {"type": "assistant", "sessionId": "s1", "message": {"model": "<synthetic>", "usage": {"input_tokens": 999}}},
          "not an object")
    report = TokenLedger(projects).report(accounts=accounts())
    session = report["sessions"][0]
    assert report["sessionCount"] == 1 and session["sessionId"] == "s1" and session["project"] == "parser"
    assert session["messages"] == 3
    assert (session["input"], session["output"], session["cacheWrite"], session["cacheRead"]) == (16, 31, 30, 40)
    assert session["total"] == 117 == report["totals"]["total"]
    assert [m["model"] for m in session["models"]] == ["claude-opus-5-5", "claude-haiku-4-5-20251001"]


def test_a_resumed_session_that_repeats_earlier_replies_is_not_double_counted(tmp_path):
    projects = tmp_path / "projects"
    write(projects / "p" / "old.jsonl", reply("old", 10, message_id="m1"))
    write(projects / "p" / "new.jsonl", reply("new", 10, message_id="m1"), reply("new", 50, message_id="m2"))
    import os
    os.utime(projects / "p" / "old.jsonl", (T0, T0))
    os.utime(projects / "p" / "new.jsonl", (T0 + 100, T0 + 100))
    report = TokenLedger(projects).report()
    assert report["totals"]["messages"] == 2
    assert {s["sessionId"]: s["messages"] for s in report["sessions"]} == {"old": 1, "new": 1}


def test_one_session_worked_by_two_accounts_is_split_by_who_was_active(tmp_path):
    projects = tmp_path / "projects"
    write(projects / "p" / "s1.jsonl", reply("s1", -50), reply("s1", 10), reply("s1", 20), reply("s1", 110, tokens=(1, 1, 1, 1)))
    record = [(T0, "a@example.com", 1), (T0 + 100, "B@example.com", 2)]
    report = TokenLedger(projects).report(history=record, accounts=accounts(), live_ids=["s1"])
    session = report["sessions"][0]
    assert session["live"] is True
    split = {part["name"]: part["total"] for part in session["accounts"]}
    assert split == {"work": 200, "home": 4, None: 100}  # the reply before any record stays unattributed
    by_account = {a["name"]: a for a in report["accounts"]}
    assert by_account["work"]["total"] == 200 and by_account["work"]["sessions"] == 1
    assert by_account["home"]["total"] == 4 and by_account["home"]["models"][0]["model"] == "claude-opus-5-5"
    assert by_account[None]["email"] is None and by_account[None]["total"] == 100
    assert sum(a["total"] for a in report["accounts"]) == report["totals"]["total"]


def test_only_new_lines_are_read_on_the_next_look_and_a_half_written_line_waits(tmp_path):
    projects = tmp_path / "projects"
    path = projects / "p" / "s1.jsonl"
    write(path, reply("s1", 10), partial=reply("s1", 20))
    ledger = TokenLedger(projects)
    assert ledger.report()["totals"]["messages"] == 1
    path.write_text("".join(json.dumps(reply("s1", offset)) + "\n" for offset in (10, 20, 30)), encoding="utf-8")
    assert ledger.report()["totals"]["messages"] == 3
    path.write_text(json.dumps(reply("s1", 10)) + "\n", encoding="utf-8")  # rewritten shorter
    assert ledger.report()["totals"]["messages"] == 1
    path.unlink()
    assert ledger.report()["totals"]["messages"] == 0


def test_a_missing_folder_or_damaged_file_gives_an_empty_report_not_an_error(tmp_path):
    assert TokenLedger(tmp_path / "nowhere").report()["totals"]["total"] == 0
    projects = tmp_path / "projects"
    (projects / "p").mkdir(parents=True)
    (projects / "p" / "bad.jsonl").write_bytes(b'\xff\xfe "usage" "assistant" {{{\n')
    assert TokenLedger(projects).report()["sessions"] == []


def test_the_report_never_carries_message_text(tmp_path):
    projects = tmp_path / "projects"
    write(projects / "p" / "s1.jsonl", reply("s1", 10))
    assert "SECRET" not in json.dumps(TokenLedger(projects).report(accounts=accounts()))


# ---- the record of which login was active ----

def test_history_notes_only_changes_and_survives_damage(tmp_path):
    work, home = row(1, "a@example.com", "work"), row(2, "b@example.com", "home")
    assert history.note_active(tmp_path, work, now=1.0) is True
    assert history.note_active(tmp_path, work, now=2.0) is False
    assert history.note_active(tmp_path, home, now=3.0) is True
    assert history.note_active(tmp_path, None) is False
    with (tmp_path / history.FILE).open("a") as handle:
        handle.write("{broken\n[1]\n")
    assert history.read(tmp_path / history.FILE) == [(1.0, "a@example.com", 1), (3.0, "b@example.com", 2)]


def test_switching_and_the_page_both_record_the_active_login(tmp_path):
    engine = Engine(accounts(), login="a@example.com")
    engine.backup_dir = tmp_path / "store"
    svc = service(tmp_path, engine)
    product_module("sessioner.web.api").build_state(svc)
    svc.switch("home")
    assert [entry[1] for entry in history.read(engine.backup_dir / history.FILE)] == ["a@example.com", "b@example.com"]


def test_the_page_gets_a_token_report_for_the_isolated_profile(tmp_path):
    api = product_module("sessioner.web.api")
    engine = Engine(accounts(), login="a@example.com")
    engine.backup_dir = tmp_path / "store"
    svc = service(tmp_path, engine)
    write(svc.settings_path.parent / "projects" / "p" / "s1.jsonl", reply("s1", 10))
    report = api.build_tokens(svc, *api.token_inputs(svc))["tokens"]
    assert report["available"] is True and report["totals"]["total"] == 100
    assert [a["name"] for a in report["accounts"]][:2] == ["work", "home"]


# ---- renaming ----

class Renaming(Engine):
    def set_alias(self, identifier, alias):
        self.calls.append(("alias", identifier, alias))
        next(account for account in self.rows if str(account["number"]) == identifier)["alias"] = alias
        return identifier, alias


def test_an_account_can_be_renamed_by_name_or_number(tmp_path):
    engine = Renaming(accounts(), login="a@example.com")
    svc = service(tmp_path, engine)
    assert svc.rename("work", "Office")["alias"] == "office"
    assert svc.rename("2", "personal")["alias"] == "personal"
    assert [account["alias"] for account in engine.rows] == ["office", "personal"]


@pytest.mark.parametrize("target, name, message", [
    ("work", "home", "already in use"),
    ("work", "123", "nonnumeric"),
    ("work", "bad name!", "nonnumeric"),
    ("nobody", "fine", "No saved account"),
])
def test_bad_renames_are_refused_without_changing_anything(tmp_path, target, name, message):
    engine = Renaming(accounts(), login="a@example.com")
    svc = service(tmp_path, engine)
    with pytest.raises(product_module("sessioner.service").SessionerError, match=message):
        svc.rename(target, name)
    assert [account["alias"] for account in engine.rows] == ["work", "home"]


def test_rename_from_the_page_and_the_command_line(tmp_path, monkeypatch):
    api = product_module("sessioner.web.api")
    engine = Renaming(accounts(), login="a@example.com")
    svc = service(tmp_path, engine)
    result = api.act(svc, "/api/rename", {"target": "1", "name": "office"})
    assert result["message"] == "Renamed to office (a@example.com)." or "office" in result["message"]
    assert result["state"]["accounts"][0]["name"] == "office"
    with pytest.raises(product_module("sessioner.service").SessionerError):
        api.act(svc, "/api/rename", {"target": "1"})
    code, output = invoke(monkeypatch, ["rename", "office", "work2"], svc)[:2]
    assert code == 0 and "Renamed to" in output and engine.rows[0]["alias"] == "work2"


def test_the_hook_records_the_login_it_switched_to(tmp_path):
    from sessioner.accounts import quota_hook
    from test_switch_robustness import EVENT, three
    from test_watcher import NOW

    engine = three(tmp_path)
    assert quota_hook.rotate_account(EVENT, engine, now=NOW.timestamp())["status"] == "switched"
    assert history.read(tmp_path / history.FILE) == [(NOW.timestamp(), "u2@example.com", 2)]


def test_the_hook_notes_each_call_without_account_or_message_data(tmp_path, monkeypatch):
    import io, sys
    from sessioner.accounts import paths, quota_hook

    assert quota_hook.last_call(tmp_path) == {"at": None, "status": None}
    monkeypatch.setattr(paths, "get_backup_root", lambda: tmp_path)
    monkeypatch.setattr(sys, "stdin", type("In", (), {"buffer": io.BytesIO(b'{"hook_event_name": "Stop", "secret": "do not keep"}')})())
    assert quota_hook._run_hook() == 0
    seen = quota_hook.last_call(tmp_path)
    assert seen["status"] == "ignored" and seen["at"] > 0
    assert "secret" not in (tmp_path / quota_hook.TRACE_FILE).read_text()
    monkeypatch.setattr(sys, "stdin", type("In", (), {"buffer": io.BytesIO(b"not json")})())
    quota_hook._run_hook()
    assert quota_hook.last_call(tmp_path)["status"] == "unreadable"
    (tmp_path / quota_hook.TRACE_FILE).write_text('{"at": "soon", "status": "x"}')
    assert quota_hook.last_call(tmp_path) == {"at": None, "status": None}


def test_the_page_reports_the_last_hook_call(tmp_path):
    from sessioner.accounts import quota_hook

    api = product_module("sessioner.web.api")
    engine = Engine(accounts(), login="a@example.com")
    engine.backup_dir = tmp_path / "store"
    svc = service(tmp_path, engine)
    assert api.build_state(svc)["hook"] == {"lastCalledAt": None, "lastStatus": None}
    quota_hook.note_call(engine.backup_dir, "switched", now=T0)
    assert api.build_state(svc)["hook"]["lastStatus"] == "switched"
