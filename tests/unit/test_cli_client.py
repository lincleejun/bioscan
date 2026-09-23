import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from bioscan.cli import client

EVENTS = [{"type": "progress", "product": "identify", "done": 1, "total": 1},
          {"type": "result", "path": "/a.jpg"},
          {"type": "done", "ok": 1, "failed": 0, "elapsed_ms": 12}]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"status": "ok"}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if not req["inputs"]:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"detail": "empty inputs"}')
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for e in EVENTS:  # one chunk per event, blank line in between to test skipping
            data = (json.dumps(e) + "\n\n").encode()
            self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
        self.wfile.write(b"0\r\n\r\n")


@pytest.fixture
def url():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_stream_ndjson_lines(url):
    lines = list(client.run({"inputs": [{"path": "/a.jpg"}], "want": ["identify"]}, url))
    assert [json.loads(x) for x in lines] == EVENTS
    assert client.health(url) == {"status": "ok"}


def test_http_400_is_service_error(url):
    with pytest.raises(client.ServiceError, match="HTTP 400.*empty inputs"):
        list(client.run({"inputs": [], "want": ["identify"]}, url))


def test_unreachable_is_clear_error():
    with pytest.raises(client.ServiceError, match="bioscan serve"):
        client.health("http://127.0.0.1:1")


def test_health_non_json_is_service_error():
    class Plain(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    srv = HTTPServer(("127.0.0.1", 0), Plain)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pytest.raises(client.ServiceError, match="did not return JSON"):
            client.health(f"http://127.0.0.1:{srv.server_port}")
    finally:
        srv.shutdown()
