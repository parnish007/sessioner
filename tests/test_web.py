"""The browser interface: stage logic, actions, and the loopback server's guards."""

import http.client
import json
import threading

import pytest

from test_cli import Engine, product_module, row, service

USAGE = {"fiveHour": {"pct": 42}, "sevenDay": {"pct": 10}}


@pytest.fixture
def api():
    return product_module("sessioner.web.api")


def stage(api, svc):
    return api.build_state(svc)["stage"]


def test_stages_follow_setup_from_no_login_to_armed(tmp_path, api):
    engine = Engine()
    svc = service(tmp_path, engine)
    assert stage(api, svc) == "no-login"

    engine.login = "me@example.com"
    state = api.build_state(svc)
    assert state["stage"] == "save-current"
    assert state["suggestedName"] == "primary"

    engine.rows.append(row(1, "me@example.com", "primary", usage=USAGE))
    assert stage(api, svc) == "need-second"

    engine.rows.append(row(2, "other@example.com", "backup", usage=USAGE))
    assert stage(api, svc) == "ready"

    api.act(svc, "/api/automatic", {"enabled": True})
    state = api.build_state(svc)
    assert state["stage"] == "armed"
    assert state["automatic"]["on"] is True
    assert state["backupReady"] is True


def test_next_backup_matches_the_account_the_hook_would_choose(tmp_path, api):
    engine = Engine([
        row(1, "a@x.com", "work", usage=USAGE),
        row(2, "A@x.com", "same", usage=USAGE),            # same login: never a backup
        row(3, "c@x.com", "off", disabled=True, usage=USAGE),
        row(4, "d@x.com", "spent", usage={"fiveHour": {"pct": 100}, "sevenDay": {"pct": 5}}),
        row(5, "e@x.com", "ready", usage=USAGE),
    ], login="a@x.com")
    assert api.build_state(service(tmp_path, engine))["nextBackup"] == 5
    engine.rows.pop()
    assert api.build_state(service(tmp_path, engine))["nextBackup"] is None


def test_a_missing_claude_is_reported_before_anything_else(tmp_path, api):
    svc = service(tmp_path, Engine([row(1, "a@x.com", "a")], login="a@x.com"), claude=False)
    assert stage(api, svc) == "no-claude"


def test_state_exposes_only_display_fields(tmp_path, api):
    engine = Engine([row(1, "a@x.com", "work", usage=USAGE)], login="a@x.com")
    engine.rows[0]["accessToken"] = "sk-secret"
    state = api.build_state(service(tmp_path, engine))
    assert "sk-secret" not in json.dumps(state)
    assert set(state["accounts"][0]) == {"number", "name", "email", "label", "active", "disabled", "sameLoginAs", "usage"}
    windows = state["accounts"][0]["usage"]["windows"]
    assert [(w["label"], w["pct"]) for w in windows] == [("5-hour", 42), ("Weekly", 10)]


def test_unknown_usage_gets_a_plain_note_instead_of_zero(tmp_path, api):
    engine = Engine([row(1, "a@x.com", "a")], login="a@x.com")
    usage = api.build_state(service(tmp_path, engine))["accounts"][0]["usage"]
    assert usage["windows"] == [] and usage["note"] and usage["ready"] is False


def test_the_same_login_saved_twice_is_flagged(tmp_path, api):
    engine = Engine([row(1, "a@x.com", "one", usage=USAGE), row(2, "A@x.com", "two", usage=USAGE)], login="a@x.com")
    accounts = api.build_state(service(tmp_path, engine))["accounts"]
    assert accounts[0]["sameLoginAs"] is None
    assert accounts[1]["sameLoginAs"] == "one (a@x.com)"


def test_add_and_switch_actions_confirm_in_plain_words(tmp_path, api):
    engine = Engine([row(1, "a@x.com", "work", usage=USAGE)], login="a@x.com")
    svc = service(tmp_path, engine)
    engine.rows.append(row(2, "b@x.com", "home", usage=USAGE))

    result = api.act(svc, "/api/switch", {"target": "home"})
    assert result["message"] == "Switched to home (b@x.com)."
    assert [a["active"] for a in result["state"]["accounts"]] == [False, True]

    result = api.act(svc, "/api/switch", {"target": "home"})
    assert result["message"].startswith("Already using")


def test_bad_input_is_refused_without_touching_accounts(tmp_path, api):
    service_module = product_module("sessioner.service")
    engine = Engine([row(1, "a@x.com", "work", usage=USAGE)], login="a@x.com")
    svc = service(tmp_path, engine)
    for route, body in (("/api/switch", {}), ("/api/switch", {"target": 7}), ("/api/automatic", {"enabled": "yes"}), ("/api/add", {"name": "x" * 65})):
        with pytest.raises(service_module.SessionerError):
            api.act(svc, route, body)
    with pytest.raises(KeyError):
        api.act(svc, "/api/nope", {})
    assert ("switch", "1") not in engine.calls


# ---- server ----

class Client:
    def __init__(self, server):
        self.server = server
        self.port = server.server_port

    def request(self, method, path, *, body=None, token=True, host=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        sent = {"Host": host or f"127.0.0.1:{self.port}"}
        if token:
            sent["X-Sessioner-Token"] = self.server.token
        if body is not None:
            sent["Content-Type"] = "application/json"
        sent.update(headers or {})
        connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=sent)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, data


@pytest.fixture
def running(tmp_path):
    server_module = product_module("sessioner.web.server")
    engine = Engine([row(1, "a@x.com", "work", usage=USAGE), row(2, "b@x.com", "home", usage=USAGE)], login="a@x.com")
    server = server_module.SessionerServer(service(tmp_path, engine))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield Client(server), engine
    server.shutdown()
    server.server_close()


def test_page_needs_the_launch_token_and_embeds_it(running):
    client, _ = running
    assert client.request("GET", "/", token=False)[0] == 403
    assert client.request("GET", "/?t=wrong", token=False)[0] == 403
    status, page = client.request("GET", f"/?t={client.server.token}", token=False)
    assert status == 200 and client.server.token.encode() in page and b"__TOKEN__" not in page
    assert client.request("GET", "/app.js", token=False)[0] == 200
    assert client.request("GET", "/app.css", token=False)[0] == 200


def test_api_rejects_missing_or_wrong_tokens_and_foreign_hosts(running):
    client, _ = running
    assert client.request("GET", "/api/state", token=False)[0] == 401
    assert client.request("GET", "/api/state", token=False, headers={"X-Sessioner-Token": "nope"})[0] == 401
    assert client.request("GET", "/api/state", host="evil.example")[0] == 403
    assert client.request("GET", "/api/state", host=f"localhost:{client.port}")[0] == 200


def test_posts_reject_foreign_origins_wrong_types_and_oversized_bodies(running):
    client, engine = running
    switch = {"target": "home"}
    assert client.request("POST", "/api/switch", body=switch, headers={"Origin": "https://evil.example"})[0] == 403
    assert client.request("POST", "/api/switch", body=switch, headers={"Content-Type": "text/plain"})[0] == 415
    assert client.request("POST", "/api/switch", body={"target": "x" * 20000})[0] == 413
    assert not [call for call in engine.calls if call[0] == "switch"]
    status, _ = client.request("POST", "/api/switch", body=switch, headers={"Origin": f"http://127.0.0.1:{client.port}"})
    assert status == 200
    assert ("switch", "2") in engine.calls


def test_engine_problems_come_back_as_readable_errors_not_crashes(running):
    client, _ = running
    status, data = client.request("POST", "/api/switch", body={"target": "nobody"})
    assert status == 409
    assert "No saved account matches" in json.loads(data)["error"]
    assert client.request("POST", "/api/unknown", body={})[0] == 404
    assert client.request("GET", "/etc/passwd")[0] == 404


def test_responses_forbid_framing_caching_and_outside_connections(running):
    client, _ = running
    connection = http.client.HTTPConnection("127.0.0.1", client.port, timeout=5)
    connection.request("GET", f"/?t={client.server.token}", headers={"Host": f"127.0.0.1:{client.port}"})
    response = connection.getresponse()
    response.read()
    assert response.getheader("Cache-Control") == "no-store"
    csp = response.getheader("Content-Security-Policy")
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
    assert response.getheader("Referrer-Policy") == "no-referrer"
    connection.close()


def test_quit_stops_the_server(running):
    client, _ = running
    status, data = client.request("POST", "/api/quit", body={})
    assert status == 200 and b"stopped" in data


def test_a_reload_keeps_working_through_a_same_site_cookie_but_a_wrong_one_does_not(running):
    client, _ = running
    connection = http.client.HTTPConnection("127.0.0.1", client.port, timeout=5)
    host = {"Host": f"127.0.0.1:{client.port}"}
    connection.request("GET", f"/?t={client.server.token}", headers=host)
    response = connection.getresponse(); response.read()
    cookie = response.getheader("Set-Cookie")
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    pair = cookie.split(";")[0]
    connection.close()

    assert client.request("GET", "/", token=False, headers={"Cookie": pair})[0] == 200
    assert client.request("GET", "/", token=False, headers={"Cookie": "sessioner-launch=wrong"})[0] == 403
    assert client.request("GET", "/", token=False)[0] == 403


def test_static_files_never_use_inline_scripts_styles_or_unsafe_dom_sinks():
    from pathlib import Path
    import re

    static = Path(product_module("sessioner.web.server").__file__).parent / "static"
    page = (static / "index.html").read_text(encoding="utf-8")
    script = (static / "app.js").read_text(encoding="utf-8")
    assert " style=" not in page and not re.search(r"<script(?![^>]*\ssrc=)", page)
    assert "innerHTML" not in script and 'setAttribute("style"' not in script and "eval(" not in script


def test_token_report_needs_the_token_and_answers_without_message_text(running):
    client, _ = running
    assert client.request("GET", "/api/tokens", token=False)[0] == 401
    status, data = client.request("GET", "/api/tokens")
    report = json.loads(data)["tokens"]
    assert status == 200 and report["available"] is True and report["totals"]["total"] == 0


def test_the_animated_logo_is_served_for_both_themes(running):
    client, _ = running
    for name in ("/logo-light.gif", "/logo-dark.gif"):
        status, data = client.request("GET", name, token=False)
        assert status == 200 and data[:6] in (b"GIF89a", b"GIF87a")
