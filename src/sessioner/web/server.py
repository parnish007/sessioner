"""A loopback-only HTTP server for the Sessioner browser interface.

Everything stays on this computer: the server binds to 127.0.0.1, every API call
needs a per-launch token, and Host and Origin headers are checked so another
website cannot drive it.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import parse_qs, urlsplit
import webbrowser

from sessioner.display import say
from sessioner.service import SessionerError
from sessioner.web import api

STATIC = Path(__file__).parent / "static"
ASSETS = {"/app.css": "text/css; charset=utf-8", "/app.js": "text/javascript; charset=utf-8"}
MAX_BODY = 16 * 1024
_CSP = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"


class SessionerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, service, port: int = 0):
        super().__init__(("127.0.0.1", port), _Handler)
        self.service = service
        self.token = secrets.token_urlsafe(24)
        # The account engine is not built for concurrent writers.
        self.lock = threading.Lock()

    @property
    def allowed_hosts(self) -> set[str]:
        return {f"127.0.0.1:{self.server_port}", f"localhost:{self.server_port}"}

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}/?t={self.token}"


class _Handler(BaseHTTPRequestHandler):
    server_version = "Sessioner"
    sys_version = ""

    def log_message(self, format, *args):
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", _CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _trusted_host(self) -> bool:
        if self.headers.get("Host", "") not in self.server.allowed_hosts:
            self._json(403, {"error": "This address is not allowed."})
            return False
        return True

    def _valid_token(self, supplied: str | None) -> bool:
        try:
            return supplied is not None and secrets.compare_digest(supplied.encode("utf-8"), self.server.token.encode("utf-8"))
        except (TypeError, ValueError):
            return False

    def _authorized(self) -> bool:
        if not self._valid_token(self.headers.get("X-Sessioner-Token")):
            self._json(401, {"error": "Open Sessioner again from the terminal to get a fresh link."})
            return False
        return True

    def do_GET(self):
        if not self._trusted_host():
            return
        parts = urlsplit(self.path)
        if parts.path == "/":
            if not self._valid_token((parse_qs(parts.query).get("t") or [None])[0]):
                self._send(403, b"Open this page with the link that sessioner ui prints.", "text/plain; charset=utf-8")
                return
            page = (STATIC / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", self.server.token)
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
        elif parts.path in ASSETS:
            self._send(200, (STATIC / parts.path.lstrip("/")).read_bytes(), ASSETS[parts.path])
        elif parts.path == "/api/state":
            if self._authorized():
                self._run(lambda: {"state": api.build_state(self.server.service)})
        else:
            self._json(404, {"error": "Not found."})

    def do_POST(self):
        if not self._trusted_host() or not self._authorized():
            return
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {f"http://{host}" for host in self.server.allowed_hosts}:
            self._json(403, {"error": "This request did not come from the Sessioner page."})
            return
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            self._json(415, {"error": "Send JSON."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            self._json(413, {"error": "That request is too large."})
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except (UnicodeError, json.JSONDecodeError):
            self._json(400, {"error": "That request could not be read."})
            return
        path = urlsplit(self.path).path
        if path == "/api/quit":
            self._json(200, {"message": "Sessioner stopped. You can close this tab."})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        self._run(lambda: api.act(self.server.service, path, body))

    def _run(self, action) -> None:
        try:
            with self.server.lock:
                result = action()
        except KeyError:
            self._json(404, {"error": "Not found."})
        except SessionerError as exc:
            self._json(409, {"error": str(exc)})
        except Exception:
            self._json(500, {"error": "Sessioner could not finish that. Try again, or run sessioner doctor."})
        else:
            self._json(200, result)


def run_ui(service, console, *, open_browser: bool = True, port: int = 0) -> int:
    try:
        server = SessionerServer(service, port)
    except OSError:
        raise SessionerError("That port is not available.", "sessioner ui --port 0") from None
    say(console, "Sessioner is open in your browser. Nothing leaves this computer.")
    say(console, f"If it did not open, use this link: {server.url}")
    say(console, "Press Ctrl+C here, or choose Quit in the page, to stop.")
    if open_browser:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
