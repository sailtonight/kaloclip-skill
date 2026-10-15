import json
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "skills" / "kaloclip" / "scripts")
)


class MockClip(BaseHTTPRequestHandler):
    """Scriptable gateway mock.

    server.responses[path] is a dict body (200), a (status, body) tuple (body None = empty),
    a list of those (served in order, last one repeats), or a callable(query, body).
    Default: Kaloclip success envelope with data null.
    """

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        ctype = self.headers.get("Content-Type", "")
        body = json.loads(raw) if raw and ctype.startswith("application/json") else raw
        self._respond(body)

    def do_GET(self):
        self._respond(None)

    def _respond(self, body):
        headers = {k.lower(): v for k, v in self.headers.items()}
        path, _, qs = self.path.partition("?")
        query = {k: v[0] for k, v in urllib.parse.parse_qs(qs).items()}
        self.server.requests.append((self.command, path, query, headers, body))
        spec = self.server.responses.get(path, {"success": True, "data": None})
        if isinstance(spec, list):
            spec = spec.pop(0) if len(spec) > 1 else spec[0]
        if callable(spec):
            spec = spec(query, body)
        status, payload = (spec if isinstance(spec, tuple) else (200, spec))
        raw = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


@pytest.fixture
def mock_server():
    server = HTTPServer(("127.0.0.1", 0), MockClip)
    server.responses = {}
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    yield server
    server.shutdown()


@pytest.fixture
def env(monkeypatch, mock_server, tmp_path):
    from kaloclip import api
    from kaloclip.commands import common

    monkeypatch.setenv("KALODATA_API_KEY", "test-key")
    monkeypatch.setenv("KALOCLIP_BASE_URL", f"http://127.0.0.1:{mock_server.server_address[1]}")
    monkeypatch.setenv("KALODATA_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("KALOCLIP_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(common, "POLL_SECONDS", 0.0)
    monkeypatch.setattr(api.time, "sleep", lambda s: None)
    return mock_server


def ok(data):
    return {"success": True, "code": None, "message": None, "data": data}
