#!/usr/bin/env python3
"""Key Value Copy — Backend Server

A minimal HTTP server that serves static files and provides
authenticated API endpoints for key-value data storage.

Endpoints:
  POST /api/login    — authenticate with password, returns session token
  GET  /api/check    — verify session token validity
  GET  /api/data     — get all entries + crypto key (authenticated)
  PUT  /api/data     — save all entries + crypto key (authenticated)
  POST /api/logout   — invalidate session token
  *    /*            — serve static files
"""

import http.server
import json
import mimetypes
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlparse

# ── Configuration (via environment variables) ──────────────────
DATA_DIR = Path(os.environ.get("KVC_DATA_DIR", "./data"))
STATIC_DIR = Path(os.environ.get("KVC_STATIC_DIR", "."))
PORT = int(os.environ.get("KVC_PORT", "8765"))
BIND = os.environ.get("KVC_BIND", "localhost")
SESSION_TTL = int(os.environ.get("KVC_SESSION_TTL", "86400"))  # 24 hours

# ── File paths ─────────────────────────────────────────────────
PASSWORD_FILE = DATA_DIR / "password.txt"
STORE_FILE = DATA_DIR / "store.json"

# ── In-memory session store: { token: expiry_timestamp } ───────
sessions = {}


def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_password():
    """Read the current password from disk."""
    try:
        return PASSWORD_FILE.read_text().strip()
    except FileNotFoundError:
        return None


def generate_password():
    """Generate a random password and write to disk."""
    pw = secrets.token_urlsafe(24)
    PASSWORD_FILE.write_text(pw + "\n")
    return pw


def load_store():
    """Load the data store from disk."""
    try:
        return json.loads(STORE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"entries": [], "cryptoKey": None}


def save_store(data):
    """Save the data store to disk atomically."""
    tmp = STORE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.rename(STORE_FILE)


def create_session():
    """Create a new session token, cleaning expired ones first."""
    now = time.time()
    expired = [t for t, exp in sessions.items() if exp < now]
    for t in expired:
        del sessions[t]
    token = secrets.token_hex(32)
    sessions[token] = now + SESSION_TTL
    return token


def verify_session(token):
    """Check if a session token is valid."""
    if not token:
        return False
    expiry = sessions.get(token)
    if expiry is None:
        return False
    if time.time() > expiry:
        del sessions[token]
        return False
    return True


class KVCHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler for the Key Value Copy API and static files."""

    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {format % args}", flush=True)

    # ── Response helpers ───────────────────────────────────────
    def send_json(self, status, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length))

    def get_bearer_token(self):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:]
        return None

    def require_auth(self):
        """Returns True if authorized; sends 401 and returns False otherwise."""
        token = self.get_bearer_token()
        if not verify_session(token):
            self.send_json(401, {"error": "Unauthorized"})
            return False
        return True

    # ── Static file serving ────────────────────────────────────
    def serve_static(self, path):
        """Serve a static file from STATIC_DIR."""
        if path == "/":
            path = "/index.html"

        # Security: prevent directory traversal
        try:
            file_path = (STATIC_DIR / path.lstrip("/")).resolve()
            if not str(file_path).startswith(str(STATIC_DIR.resolve())):
                self.send_error(403)
                return
        except (ValueError, OSError):
            self.send_error(400)
            return

        if not file_path.is_file():
            self.send_error(404)
            return

        content_type, _ = mimetypes.guess_type(str(file_path))
        if content_type is None:
            content_type = "application/octet-stream"

        try:
            data = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            self.send_error(500)

    # ── HTTP Methods ───────────────────────────────────────────
    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/api/data":
            if not self.require_auth():
                return
            self.send_json(200, load_store())
            return

        if path == "/api/check":
            token = self.get_bearer_token()
            if verify_session(token):
                self.send_json(200, {"authenticated": True})
            else:
                self.send_json(401, {"authenticated": False})
            return

        # Everything else: serve static files
        self.serve_static(path)

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/login":
            body = self.read_json_body()
            submitted = body.get("password", "")
            actual = get_password()
            if actual is None:
                self.send_json(500, {"error": "Server password not configured"})
                return
            if not secrets.compare_digest(submitted, actual):
                self.send_json(401, {"error": "Incorrect password"})
                return
            token = create_session()
            self.send_json(200, {"token": token})
            return

        if path == "/api/logout":
            token = self.get_bearer_token()
            if token and token in sessions:
                del sessions[token]
            self.send_json(200, {"ok": True})
            return

        self.send_json(404, {"error": "Not found"})

    def do_PUT(self):
        path = urlparse(self.path).path

        if path == "/api/data":
            if not self.require_auth():
                return
            body = self.read_json_body()
            save_store(body)
            self.send_json(200, {"ok": True})
            return

        self.send_json(404, {"error": "Not found"})


def main():
    ensure_data_dir()

    # Generate password on first run
    if not PASSWORD_FILE.exists():
        pw = generate_password()
        print(f"Generated initial password: {pw}")
    else:
        print(f"Using existing password from {PASSWORD_FILE}")

    server = http.server.HTTPServer((BIND, PORT), KVCHandler)
    print(f"Key Value Copy server listening on {BIND}:{PORT}")
    print(f"  Static files: {STATIC_DIR.resolve()}")
    print(f"  Data dir:     {DATA_DIR.resolve()}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.server_close()


if __name__ == "__main__":
    main()
