import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from scanner import scan, review_pair


class handler(BaseHTTPRequestHandler):
    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/api/scan":
            try:
                q = parse_qs(urlparse(self.path).query)
                limit = min(max(int(q.get("limit", ["40"])[0]), 1), 100)
                meteora = q.get("meteora", ["0"])[0] == "1"
                self._send_json(200, scan(limit, meteora))
            except Exception as exc:
                self._send_json(502, {"error": str(exc)})
            return

        if path == "/api/review":
            try:
                q = parse_qs(urlparse(self.path).query)
                pair = q.get("pair", [""])[0]
                if not pair:
                    raise ValueError("pair wajib diisi")
                self._send_json(200, review_pair(pair))
            except Exception as exc:
                self._send_json(502, {"error": str(exc)})
            return

        if path == "/api/healthz":
            self._send_json(200, {"ok": True})
            return

        self._send_json(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        return
