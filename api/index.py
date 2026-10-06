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
                self._send_json(200, scan())
            except Exception as exc:
                self._send_json(502, {"error": str(exc)})
            return

        if path == "/api/review":
            try:
                q = parse_qs(urlparse(self.path).query)
                pair = q.get("pair", [""])[0]
                if not pair:
                    raise ValueError("pair wajib diisi")
                horizon = min(max(int(q.get("horizon", ["96"])[0]), 1), 192)
                paths = min(max(int(q.get("paths", ["2000"])[0]), 200), 5000)
                self._send_json(200, review_pair(pair, horizon_bars=horizon, mc_paths=paths))
            except Exception as exc:
                self._send_json(502, {"error": str(exc)})
            return

        if path == "/api/healthz":
            self._send_json(200, {"ok": True})
            return

        self._send_json(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        return
