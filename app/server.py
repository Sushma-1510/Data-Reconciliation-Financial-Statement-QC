"""Local web server for the HTML/CSS dashboard (Python standard library only).

Run from the project root:   python app/server.py
Then open:                   http://localhost:8000

GET  /                -> redirect to /static/index.html
GET  /static/<file>   -> app/static/<file>
GET  /api/dashboard   -> all figures, computed from reference.db and output/
GET  /api/tickers     -> ticker_review rows (reconciliation explorer)
GET  /api/review      -> both review CSVs
POST /api/verdicts    -> save my_verdict / my_note by key (merge rule in scripts/review_file.py)
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pandas as pd

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP.parent / "scripts"))
import data  # noqa: E402
from review_file import update_verdicts  # noqa: E402

STATIC = APP / "static"
HOST, PORT = "127.0.0.1", 8000      # only reachable from this computer
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8"}
MAX_BODY = 1_000_000


def to_json(obj):
    # numpy numbers -> plain Python numbers
    return json.dumps(obj, default=lambda o: o.item() if hasattr(o, "item") else str(o)).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def error(self, status, message):
        self.send(status, to_json({"error": message}))

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path in ("/", "/index.html"):
                # redirect so the page's relative links (styles.css, app.js) resolve to /static/
                self.send_response(302)
                self.send_header("Location", "/static/index.html")
                self.send_header("Content-Length", "0")
                return self.end_headers()
            if path.startswith("/static/"):
                file = (STATIC / path.removeprefix("/static/")).resolve()
                if file.parent != STATIC.resolve() or file.suffix not in CONTENT_TYPES or not file.exists():
                    return self.error(404, "not found")
                return self.send(200, file.read_bytes(), CONTENT_TYPES[file.suffix])
            if path == "/api/dashboard":
                return self.send(200, to_json(data.dashboard()))
            if path == "/api/tickers":
                return self.send(200, to_json(data.ticker_rows()))
            if path == "/api/review":
                return self.send(200, to_json(data.review_rows()))
            return self.error(404, "not found")
        except FileNotFoundError as e:
            return self.error(500, f"missing file: {e.filename} - run scripts 01-07 first")

    def do_POST(self):
        if self.path != "/api/verdicts":
            return self.error(404, "not found")
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0 or length > MAX_BODY:
            return self.error(400, "empty or too large request")
        try:
            body = json.loads(self.rfile.read(length))
            file_name = body["file"]
            if file_name not in data.REVIEW_FILES:          # whitelist: no arbitrary paths
                return self.error(400, f"unknown review file: {file_name}")
            key = data.REVIEW_FILES[file_name]
            edits = pd.DataFrame([{key: e["key"], "my_verdict": e.get("my_verdict", ""),
                                   "my_note": e.get("my_note", "")} for e in body["edits"]])
            if edits.empty:
                return self.error(400, "no edits")
            saved = update_verdicts(data.OUT / file_name, key, edits)
            return self.send(200, to_json({"saved": saved, "file": file_name}))
        except PermissionError:
            return self.error(423, f"output/{body['file']} is locked - close it in Excel and try again")
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            return self.error(400, str(e))

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")


if __name__ == "__main__":
    if not data.DB.exists():
        sys.exit(f"{data.DB} not found - run scripts 01-07 first (see README).")
    print(f"Dashboard running at http://localhost:{PORT}  (Ctrl+C to stop)")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
