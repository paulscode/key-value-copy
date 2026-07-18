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
import re
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# 0.3.5.1 addresses the LAN interface as "<v3-onion-id>.local", so the .onion is
# the same 56-char base32 label with a different suffix. (0.4.0 uses an unrelated
# adjective-noun hostname, so this derivation only applies when env discovery is
# absent and the host matches this pattern.)
_ONION_LABEL = re.compile(r"^([a-z2-7]{56})\.(local|onion)$", re.IGNORECASE)

from store import Store

# ── Hardening knobs ────────────────────────────────────────────
MAX_BODY_BYTES = 8 * 1024 * 1024      # reject larger request bodies (DoS guard)
MAX_SYNC_CHANGES = 10000              # max entries pushed in one /api/v1/sync
MAX_VALUE_BYTES = 256 * 1024          # max serialized size of one entry value
MAX_KEY_LEN = 4096                    # max length of an entry key
RATE_MAX_FAILS = 10                   # failed attempts per window before lockout
RATE_WINDOW_S = 300                   # sliding window (seconds)

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; object-src 'none'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}

# ── Simple in-memory failure-based rate limiter (single instance) ──
_rate = {}
_rate_lock = threading.Lock()


def _fingerprint(der):
    """SHA-256 of a DER cert, colon-hex uppercase (matches `openssl -sha256`)."""
    import hashlib
    h = hashlib.sha256(der).hexdigest().upper()
    return ":".join(h[i:i + 2] for i in range(0, len(h), 2))


def _der_to_pem(der):
    """Wrap DER cert bytes as a normalized PEM string."""
    import base64
    b64 = base64.b64encode(der).decode("ascii")
    body = "\n".join(b64[i:i + 64] for i in range(0, len(b64), 64))
    return f"-----BEGIN CERTIFICATE-----\n{body}\n-----END CERTIFICATE-----\n"


def _pem_to_der(data):
    """Return DER bytes from a cert blob that may be PEM or already DER."""
    if b"-----BEGIN CERTIFICATE-----" in data:
        import base64
        m = re.search(
            rb"-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----", data, re.S
        )
        if not m:
            return None
        try:
            return base64.b64decode(re.sub(rb"\s+", b"", m.group(1)))
        except Exception:
            return None
    if data[:1] == b"\x30":  # DER SEQUENCE
        return data
    return None


def _default_gateways():
    gws = []
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                fields = line.split()
                if len(fields) > 3 and fields[1] == "00000000":
                    gw = ".".join(
                        str(int(fields[2][i:i + 2], 16)) for i in (6, 4, 2, 0)
                    )
                    if gw != "0.0.0.0":
                        gws.append(gw)
    except Exception as e:
        print(f"[ca-probe] gateway lookup failed: {e}", flush=True)
    return gws


def _lan_host_from_env():
    """The LAN hostname the SDK discovered (0.4.0), e.g. wide-treason.local —
    used as a Host header in case the host web server routes the CA by vhost."""
    m = re.match(r"https?://([^/:]+)", os.environ.get("KVC_LAN_URL") or "")
    return m.group(1) if m else None


def _ca_candidate_requests():
    """(url, host_header) pairs where the embassyOS host serves its root CA.
    The host is reachable as `embassy`/`host.containers.internal` (per /etc/hosts)
    or via the LAN gateway. 0.3.5.1 serves it at /eos/local.crt; 0.4.0 at
    /static/local-root-ca.crt. We also retry with the LAN vhost Host header."""
    hosts = ["embassy", "host.containers.internal"] + _default_gateways()
    paths = ["/eos/local.crt", "/static/local-root-ca.crt"]
    lan_host = _lan_host_from_env()
    host_headers = [None] + ([lan_host] if lan_host else [])
    reqs, seen = [], set()
    for h in hosts:
        if not h:
            continue
        for p in paths:
            url = f"http://{h}{p}"
            for hh in host_headers:
                if (url, hh) not in seen:
                    seen.add((url, hh))
                    reqs.append((url, hh))
    return reqs


def probe_ca(server):
    """Best-effort: fetch the StartOS root-CA from the host and stash its SHA-256
    fingerprint on the server for the pairing payload. Never fatal; background
    thread. Returns on the first response that parses as a certificate."""
    import urllib.request

    for url, host_header in _ca_candidate_requests():
        try:
            req = urllib.request.Request(url)
            if host_header:
                req.add_header("Host", host_header)
            with urllib.request.urlopen(req, timeout=5) as r:
                der = _pem_to_der(r.read())
            if der:
                fp = _fingerprint(der)
                server.ca_sha256 = fp
                server.ca_pem = _der_to_pem(der)   # full cert for the app's trust anchor
                extra = f" (Host: {host_header})" if host_header else ""
                print(f"[ca-probe] CA fingerprint via {url}{extra}: {fp}", flush=True)
                return
        except Exception as e:
            print(f"[ca-probe] {url}: {e}", flush=True)
    print("[ca-probe] CA not found; pairing omits ca_sha256 (device uses TOFU)",
          flush=True)


def make_pairing_qr_svg(payload_str):
    """Render `payload_str` as a QR-code SVG (dark modules on white — required
    for reliable scanning regardless of the app's dark theme). Returns None if
    the qrcode lib isn't present, in which case the UI shows the code text only."""
    try:
        import qrcode
    except Exception:
        return None
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    qr.add_data(payload_str)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)
    segs = [
        f"M{c} {r}h1v1h-1z"
        for r, row in enumerate(matrix)
        for c, dark in enumerate(row)
        if dark
    ]
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {n} {n}" shape-rendering="crispEdges">'
        f'<rect width="{n}" height="{n}" fill="#ffffff"/>'
        f'<path d="{"".join(segs)}" fill="#000000"/></svg>'
    )


def discovered_endpoints():
    """Service's own reachable addresses, supplied by the StartOS layer as env.

    On 0.4.0 these are set by the SDK-driven discovery in startos/main.ts. When
    absent (e.g. 0.3.5.1 until its discovery lands, or plain `docker run`), the
    pairing flow falls back to the origin the admin's browser is already using.
    """
    return {
        "onion_url": os.environ.get("KVC_ONION_URL") or None,
        "lan_url": os.environ.get("KVC_LAN_URL") or None,
        "lan_ip": os.environ.get("KVC_LAN_IP") or None,
        "ca_sha256": os.environ.get("KVC_CA_SHA256") or None,
    }


def rate_ok(ip, bucket):
    """False if this (ip, bucket) has too many recent failures."""
    now = time.time()
    with _rate_lock:
        arr = [t for t in _rate.get((ip, bucket), []) if t > now - RATE_WINDOW_S]
        _rate[(ip, bucket)] = arr
        return len(arr) < RATE_MAX_FAILS


def rate_fail(ip, bucket):
    now = time.time()
    with _rate_lock:
        arr = [t for t in _rate.get((ip, bucket), []) if t > now - RATE_WINDOW_S]
        arr.append(now)
        _rate[(ip, bucket)] = arr


def rate_reset(ip, bucket):
    with _rate_lock:
        _rate.pop((ip, bucket), None)

# ── Configuration (via environment variables) ──────────────────
DATA_DIR = Path(os.environ.get("KVC_DATA_DIR", "./data"))
STATIC_DIR = Path(os.environ.get("KVC_STATIC_DIR", "."))
PORT = int(os.environ.get("KVC_PORT", "8765"))
BIND = os.environ.get("KVC_BIND", "localhost")
SESSION_TTL = int(os.environ.get("KVC_SESSION_TTL", "86400"))  # 24 hours

# ── File paths ─────────────────────────────────────────────────
PASSWORD_FILE = DATA_DIR / "password.txt"
STORE_FILE = DATA_DIR / "store.json"
DB_FILE = DATA_DIR / "kvc.db"

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
    def _write_security_headers(self, no_store=False):
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        if no_store:
            self.send_header("Cache-Control", "no-store")

    def send_json(self, status, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._write_security_headers(no_store=True)  # never cache API responses
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self):
        """Return the parsed body, or None after already sending 413/400."""
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > MAX_BODY_BYTES:
            self.send_json(413, {"error": "Payload too large"})
            return None
        if length == 0:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, ValueError):
            self.send_json(400, {"error": "Invalid JSON"})
            return None

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

    def transport(self):
        """Best-effort channel classification for audit (lan vs tor)."""
        host = self.headers.get("Host", "")
        return "tor" if host.endswith(".onion") else "lan"

    def resolve_endpoints(self):
        """Endpoints for pairing: env-based discovery (0.4.0) if present, else
        derived from the request Host (0.3.5.1's <onion-id>.local ⇄ .onion)."""
        disc = discovered_endpoints()
        # CA fingerprint: prefer an explicit env value, else the probe result.
        if not disc["ca_sha256"]:
            disc["ca_sha256"] = getattr(self.server, "ca_sha256", None)
        if any(disc[k] for k in ("onion_url", "lan_url", "lan_ip")):
            return disc  # 0.4.0 SDK discovery already provided the URLs
        host_full = self.headers.get("Host", "")
        m = _ONION_LABEL.match(host_full.split(":")[0])
        if m:
            label = m.group(1).lower()
            disc["lan_url"] = f"https://{label}.local"
            disc["onion_url"] = f"http://{label}.onion"
        return disc

    def require_sync_auth(self):
        """Auth for /api/v1/* data endpoints: a web session OR an active device
        API key (Bearer kvc_...). Device keys are refused when the API is off,
        and repeated bad API keys are rate-limited (like login/pair)."""
        token = self.get_bearer_token()
        if verify_session(token):
            return True
        if token and token.startswith("kvc_"):
            ip = self.client_address[0]
            if not rate_ok(ip, "apikey"):
                self.send_json(429, {"error": "Too many attempts. Try again later."})
                return False
            if (getattr(self.server, "api_enabled", True)
                    and self.server.store.verify_api_key(token, transport=self.transport())):
                return True
            rate_fail(ip, "apikey")
        self.send_json(401, {"error": "Unauthorized"})
        return False

    def validate_sync_changes(self, changes):
        """Bound a sync push (DoS guard). Sends 413 and returns False if over."""
        if not isinstance(changes, list):
            self.send_json(400, {"error": "changes must be a list"})
            return False
        if len(changes) > MAX_SYNC_CHANGES:
            self.send_json(413, {"error": "Too many changes in one sync"})
            return False
        for c in changes:
            if not isinstance(c, dict):
                self.send_json(400, {"error": "each change must be an object"})
                return False
            if len(str(c.get("key", ""))) > MAX_KEY_LEN:
                self.send_json(413, {"error": "Entry key too long"})
                return False
            val = c.get("value")
            if val is not None and len(json.dumps(val)) > MAX_VALUE_BYTES:
                self.send_json(413, {"error": "Entry value too large"})
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
            self._write_security_headers()
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
            self.send_json(200, self.server.store.load_all())
            return

        if path == "/api/check":
            token = self.get_bearer_token()
            if verify_session(token):
                self.send_json(200, {"authenticated": True,
                                     "api_enabled": self.server.api_enabled})
            else:
                self.send_json(401, {"authenticated": False})
            return

        if path == "/api/v1/bootstrap":
            if not self.require_sync_auth():
                return
            self.send_json(200, self.server.store.bootstrap())
            return

        if path == "/api/v1/changes":
            if not self.require_sync_auth():
                return
            qs = parse_qs(urlparse(self.path).query)
            since = int((qs.get("since", ["0"])[0]) or 0)
            self.send_json(200, self.server.store.changes_since(since))
            return

        if path == "/api/v1/vault-key":
            # The E2E key for the web UI, over the web session only. Devices
            # receive it via the pairing exchange, never here.
            if not self.require_auth():
                return
            self.send_json(200, {"cryptoKey": self.server.store.get_crypto_key()})
            return

        if path == "/api/keys":
            if not self.require_auth():
                return
            self.send_json(200, {"keys": self.server.store.list_keys()})
            return

        if path == "/api/enrollment":
            # Discovered endpoints + CA fingerprint. Web session (to build the
            # pairing payload) or a device API key (to fetch the CA over Tor).
            if not self.require_sync_auth():
                return
            disc = self.resolve_endpoints()
            host = self.headers.get("Host", "")
            scheme = "http" if host.endswith(".onion") else "https"
            disc["request_origin"] = f"{scheme}://{host}" if host else None
            if any(discovered_endpoints()[k] for k in ("onion_url", "lan_url", "lan_ip")):
                disc["source"] = "env"
            elif disc.get("lan_url") or disc.get("onion_url"):
                disc["source"] = "derived"
            else:
                disc["source"] = "request"
            disc["ca_pem"] = getattr(self.server, "ca_pem", None)
            self.send_json(200, disc)
            return

        # Everything else: serve static files
        self.serve_static(path)

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/login":
            ip = self.client_address[0]
            if not rate_ok(ip, "login"):
                self.send_json(429, {"error": "Too many attempts. Try again later."})
                return
            body = self.read_json_body()
            if body is None:
                return
            submitted = body.get("password", "")
            actual = get_password()
            if actual is None:
                self.send_json(500, {"error": "Server password not configured"})
                return
            if not secrets.compare_digest(submitted, actual):
                rate_fail(ip, "login")
                self.send_json(401, {"error": "Incorrect password"})
                return
            rate_reset(ip, "login")
            token = create_session()
            self.send_json(200, {"token": token, "api_enabled": self.server.api_enabled})
            return

        if path == "/api/keys":
            if not self.require_auth():
                return
            if not self.server.api_enabled:
                self.send_json(403, {"error": "Device API is disabled"})
                return
            body = self.read_json_body()
            if body is None:
                return
            pending = self.server.store.create_pending_key(body.get("label", ""))
            disc = self.resolve_endpoints()
            pairing = {
                "v": 1,
                "server_id": self.server.store.server_id,
                "enroll_code": pending["enroll_code"],
                "enroll_exp": pending["enroll_expires"],
                "onion_url": disc["onion_url"],
                "lan_url": disc["lan_url"],
                "lan_ip": disc["lan_ip"],
                "ca_sha256": disc["ca_sha256"],
            }
            # QR encodes only what a device needs to connect: drop null fields
            # and server_id (the app reads that from /api/v1/bootstrap) to keep
            # the code less dense and easier to scan off a screen.
            qr_payload = {
                k: v for k, v in pairing.items()
                if v is not None and k != "server_id"
            }
            qr_svg = make_pairing_qr_svg(json.dumps(qr_payload, separators=(",", ":")))
            self.send_json(201, {
                "key": {"id": pending["id"], "label": pending["label"],
                        "status": pending["status"], "created": pending["created"]},
                "pairing": pairing,
                "pairing_qr": qr_svg,
            })
            return

        if path == "/api/v1/pair":
            # Device claims a one-time enrollment code -> long-term key + E2E key.
            # No API-key/session auth (the device has none yet); gated by the code.
            if not self.server.api_enabled:
                self.send_json(403, {"error": "Device API is disabled"})
                return
            ip = self.client_address[0]
            if not rate_ok(ip, "pair"):
                self.send_json(429, {"error": "Too many attempts. Try again later."})
                return
            body = self.read_json_body()
            if body is None:
                return
            res = self.server.store.claim_enrollment(
                body.get("enroll_code", ""), body.get("label")
            )
            if res is None:
                rate_fail(ip, "pair")
                self.send_json(401, {"error": "Invalid or expired enrollment code"})
                return
            rate_reset(ip, "pair")
            res["e2e_key"] = self.server.store.get_crypto_key()
            # The root CA (PEM) so a device that paired over Tor can build a LAN
            # trust anchor without a separate fetch. Public info; may be null.
            res["ca_pem"] = getattr(self.server, "ca_pem", None)
            self.send_json(200, res)
            return

        if path == "/api/logout":
            token = self.get_bearer_token()
            if token and token in sessions:
                del sessions[token]
            self.send_json(200, {"ok": True})
            return

        if path == "/api/v1/sync":
            if not self.require_sync_auth():
                return
            body = self.read_json_body()
            if body is None:
                return
            changes = body.get("changes", [])
            if not self.validate_sync_changes(changes):
                return
            result = self.server.store.apply_sync(
                body.get("since", 0), changes, body.get("device_hlc"),
            )
            self.send_json(200, result)
            return

        self.send_json(404, {"error": "Not found"})

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith("/api/keys/"):
            if not self.require_auth():
                return
            kid = path[len("/api/keys/"):]
            if self.server.store.revoke_key(kid):
                self.send_json(200, {"ok": True})
            else:
                self.send_json(404, {"error": "No such device"})
            return
        self.send_json(404, {"error": "Not found"})

    def do_PATCH(self):
        path = urlparse(self.path).path
        if path.startswith("/api/keys/"):
            if not self.require_auth():
                return
            body = self.read_json_body()
            if body is None:
                return
            kid = path[len("/api/keys/"):]
            if self.server.store.rename_key(kid, body.get("label", "")):
                self.send_json(200, {"ok": True})
            else:
                self.send_json(404, {"error": "No such device"})
            return
        self.send_json(404, {"error": "Not found"})

    def do_PUT(self):
        path = urlparse(self.path).path

        if path == "/api/data":
            if not self.require_auth():
                return
            body = self.read_json_body()
            if body is None:
                return
            self.server.store.replace_all(
                body.get("entries", []), body.get("cryptoKey")
            )
            self.send_json(200, {"ok": True})
            return

        if path == "/api/v1/vault-key":
            if not self.require_auth():
                return
            body = self.read_json_body()
            if body is None:
                return
            if body.get("cryptoKey") is not None:
                self.server.store.set_crypto_key(body["cryptoKey"])
            self.send_json(200, {"ok": True})
            return

        self.send_json(404, {"error": "Not found"})


def open_store():
    """Open the SQLite store, importing a legacy store.json on first run."""
    store = Store(DB_FILE)
    if store.is_empty() and STORE_FILE.exists():
        try:
            n = store.migrate_from_json(STORE_FILE)
            STORE_FILE.rename(STORE_FILE.with_suffix(".json.migrated"))
            print(f"Migrated {n} entries from store.json into {DB_FILE.name}")
        except Exception as exc:  # keep serving even if the import hiccups
            print(f"WARNING: store.json migration failed: {exc}")
    return store


def main():
    ensure_data_dir()

    # Generate password on first run
    if not PASSWORD_FILE.exists():
        pw = generate_password()
        print(f"Generated initial password: {pw}")
    else:
        print(f"Using existing password from {PASSWORD_FILE}")

    server = http.server.ThreadingHTTPServer((BIND, PORT), KVCHandler)
    server.daemon_threads = True
    server.store = open_store()

    # Apply StartOS config knobs (conflict/delete policy, tombstone retention).
    server.store.configure(
        conflict_strategy=os.environ.get("KVC_CONFLICT_STRATEGY"),
        delete_strategy=os.environ.get("KVC_DELETE_STRATEGY"),
        tombstone_days=os.environ.get("KVC_TOMBSTONE_DAYS"),
    )
    server.api_enabled = os.environ.get("KVC_ENABLE_API", "true").lower() != "false"
    print(f"  Device API: {'enabled' if server.api_enabled else 'disabled'}")
    print(f"  Policy:     {server.store.policy()} | tombstones {server.store.tombstone_days()}d")

    server.ca_sha256 = os.environ.get("KVC_CA_SHA256") or None
    server.ca_pem = None
    # Discover the root-CA (fingerprint + PEM) in the background (never blocks).
    threading.Thread(target=probe_ca, args=(server,), daemon=True).start()

    # Periodic tombstone compaction (also runs once at startup).
    def _compact_loop():
        while True:
            try:
                n = server.store.compact_tombstones()
                if n:
                    print(f"Compacted {n} expired tombstone(s)", flush=True)
            except Exception as exc:
                print(f"compaction error: {exc}", flush=True)
            time.sleep(6 * 3600)

    threading.Thread(target=_compact_loop, daemon=True).start()
    print(f"Key Value Copy server listening on {BIND}:{PORT}")
    print(f"  Static files: {STATIC_DIR.resolve()}")
    print(f"  Data dir:     {DATA_DIR.resolve()}")
    print(f"  Database:     {DB_FILE.resolve()}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.server_close()
        server.store.close()


if __name__ == "__main__":
    main()
