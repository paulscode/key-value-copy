#!/usr/bin/env python3
"""SQLite data layer for Key Value Copy.

stdlib-only (`sqlite3`). Replaces the whole-file `store.json` read/modify/write
with per-entry rows plus a monotonic change-log — the foundation the device sync
protocol builds on. For now it backs the existing web `/api/data` shim so there
is no user-visible change: same data in, same data out.

Concurrency: a single connection shared across the threaded HTTP server, guarded
by one lock (appliance-scale traffic makes this ample), with WAL journalling for
durability and concurrent reads. Every public method is transaction-atomic.
"""

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from sync_engine import DEFAULT_POLICY, HLCClock, parse_hlc, resolve

SCHEMA_VERSION = 1
ORDER_GAP = 1024  # sparse gap between entry sort positions
ENROLL_TTL_MS = 5 * 60 * 1000  # enrollment code lifetime: 5 minutes


def _sha256(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS entries (
  id       TEXT PRIMARY KEY,
  key      TEXT NOT NULL,
  value    TEXT NOT NULL,           -- JSON: {iv,ct} or {_b64}
  ord      INTEGER NOT NULL,        -- sparse sort position
  created  INTEGER NOT NULL,        -- epoch ms
  rev      INTEGER NOT NULL DEFAULT 1,
  hlc      TEXT NOT NULL DEFAULT '',
  deleted  INTEGER NOT NULL DEFAULT 0,
  conflict TEXT                     -- JSON or NULL
);
CREATE TABLE IF NOT EXISTS change_log (
  seq      INTEGER PRIMARY KEY AUTOINCREMENT,
  entry_id TEXT NOT NULL,
  rev      INTEGER NOT NULL,
  hlc      TEXT NOT NULL,
  ts       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entries_ord ON entries(ord);
CREATE TABLE IF NOT EXISTS api_keys (
  id             TEXT PRIMARY KEY,       -- public key id
  label          TEXT,                   -- device name
  status         TEXT NOT NULL DEFAULT 'pending',  -- pending|active|revoked
  key_hash       TEXT,                   -- SHA-256 of the secret; null while pending
  key_prefix     TEXT,                   -- for display, e.g. kvc_ab12cd
  enroll_hash    TEXT,                   -- SHA-256 of the one-time enrollment code
  enroll_expires INTEGER,                -- code TTL (epoch ms); null once claimed
  created        INTEGER,
  last_used      INTEGER,
  last_transport TEXT,                   -- lan|tor
  revoked_at     INTEGER,
  -- A key that can dump everything is a poor trade when one vault holds
  -- personal, business and machine values side by side. `scope_prefix` limits
  -- a key to entries whose name starts with it; `read_only` stops it writing.
  -- NULL scope means unrestricted, which is what every existing key was issued
  -- as, so the column can be added without changing what already works.
  scope_prefix   TEXT,
  read_only      INTEGER NOT NULL DEFAULT 0
);
"""


def _now_ms():
    return int(time.time() * 1000)


class Store:
    def __init__(self, db_path, now_ms=_now_ms):
        self.path = str(db_path)
        self._now = now_ms
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # DELETE journal (not WAL) so kvc.db is a single self-consistent file for
        # the StartOS volume backup — no -wal/-shm sidecars to capture in sync.
        # FULL sync for durability (this is a secrets vault; writes are rare).
        self._conn.execute("PRAGMA journal_mode=DELETE")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(_SCHEMA)
        self._migrate_locked()
        self._conn.commit()
        if self._meta_get("schema_version") is None:
            self._meta_set("schema_version", str(SCHEMA_VERSION))
        if self._meta_get("vault_created") is None:
            self._meta_set("vault_created", str(int(now_ms())))
        self._conn.commit()
        self.server_id = self._ensure_server_id()
        # Node id for HLCs: short, stable, server-scoped.
        self._clock = HLCClock(self.server_id[:4], now_ms)
        self._prime_clock()

    # Added after api_keys shipped. SQLite has no ADD COLUMN IF NOT EXISTS and
    # CREATE TABLE IF NOT EXISTS never re-runs, so a column added later would
    # never appear on an install that already exists.
    _MIGRATIONS = (
        ("scope_prefix", "ALTER TABLE api_keys ADD COLUMN scope_prefix TEXT"),
        ("read_only",
         "ALTER TABLE api_keys ADD COLUMN read_only INTEGER NOT NULL DEFAULT 0"),
    )

    def _migrate_locked(self):
        have = {r[1] for r in self._conn.execute("PRAGMA table_info(api_keys)")}
        for column, sql in self._MIGRATIONS:
            if column not in have:
                self._conn.execute(sql)

    # ── meta helpers ───────────────────────────────────────────────
    def _meta_get(self, key):
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def _meta_set(self, key, value):
        self._conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def _ensure_server_id(self):
        sid = self._meta_get("server_id")
        if sid is None:
            sid = uuid.uuid4().hex
            self._meta_set("server_id", sid)
            self._conn.commit()
        return sid

    def _prime_clock(self):
        """Seed the HLC from the newest stored hlc so it stays monotonic across
        restarts (a device could otherwise see our clock go backwards)."""
        row = self._conn.execute(
            "SELECT hlc FROM change_log ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        if row and row["hlc"]:
            self._clock.update(row["hlc"])

    # ── crypto key (E2E JWK, stored server-side as today) ──────────
    def get_crypto_key(self):
        raw = self._meta_get("crypto_key")
        return json.loads(raw) if raw else None

    def set_crypto_key(self, jwk):
        with self._lock:
            self._meta_set("crypto_key", json.dumps(jwk))
            self._conn.commit()

    # ── /api/data compatibility shim ───────────────────────────────
    def load_all(self):
        """Return {entries: [...], cryptoKey} shaped for the current web app.

        `modified` is synthesised from the entry's HLC physical time so the
        existing client sees the same field it wrote. Tombstones are hidden.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM entries WHERE deleted=0 ORDER BY ord ASC, created ASC"
            ).fetchall()
            entries = []
            for r in rows:
                modified = parse_hlc(r["hlc"])[0] if r["hlc"] else r["created"]
                entries.append(
                    {
                        "id": r["id"],
                        "key": r["key"],
                        "value": json.loads(r["value"]),
                        "created": r["created"],
                        "modified": modified,
                    }
                )
            return {"entries": entries, "cryptoKey": self.get_crypto_key()}

    def replace_all(self, entries, crypto_key=None):
        """Whole-store replace (the M1 backward-compatible write path).

        The current web client PUTs the entire vault on every save, so this
        upserts each incoming entry and tombstones any that vanished — but only
        writes a new rev / change-log row for entries that actually changed, so
        the change-log stays meaningful rather than churning on every save.
        """
        with self._lock:
            now = int(self._now())
            existing = {
                r["id"]: r
                for r in self._conn.execute(
                    "SELECT * FROM entries WHERE deleted=0"
                ).fetchall()
            }
            incoming_ids = set()
            for idx, e in enumerate(entries):
                eid = e.get("id") or str(uuid.uuid4())
                incoming_ids.add(eid)
                key = e.get("key", "")
                value = json.dumps(e.get("value"))
                ordv = (idx + 1) * ORDER_GAP
                prev = existing.get(eid)
                unchanged = (
                    prev is not None
                    and prev["key"] == key
                    and prev["value"] == value
                    and prev["ord"] == ordv
                )
                if unchanged:
                    continue
                created = int(prev["created"]) if prev else int(e.get("created") or now)
                rev = (prev["rev"] + 1) if prev else 1
                hlc = self._clock.now()
                self._conn.execute(
                    "INSERT INTO entries (id,key,value,ord,created,rev,hlc,deleted,conflict) "
                    "VALUES (?,?,?,?,?,?,?,0,NULL) "
                    "ON CONFLICT(id) DO UPDATE SET key=excluded.key, value=excluded.value, "
                    "ord=excluded.ord, created=excluded.created, rev=excluded.rev, "
                    "hlc=excluded.hlc, deleted=0, conflict=NULL",
                    (eid, key, value, ordv, created, rev, hlc),
                )
                self._conn.execute(
                    "INSERT INTO change_log (entry_id,rev,hlc,ts) VALUES (?,?,?,?)",
                    (eid, rev, hlc, now),
                )
            for eid, prev in existing.items():
                if eid not in incoming_ids:
                    rev = prev["rev"] + 1
                    hlc = self._clock.now()
                    self._conn.execute(
                        "UPDATE entries SET deleted=1, rev=?, hlc=? WHERE id=?",
                        (rev, hlc, eid),
                    )
                    self._conn.execute(
                        "INSERT INTO change_log (entry_id,rev,hlc,ts) VALUES (?,?,?,?)",
                        (eid, rev, hlc, now),
                    )
            if crypto_key is not None:
                self._meta_set("crypto_key", json.dumps(crypto_key))
            self._conn.commit()

    # ── device sync protocol ───────────────────────────────────────
    def policy(self):
        return {
            "conflict_strategy": self._meta_get("conflict_strategy")
            or DEFAULT_POLICY["conflict_strategy"],
            "delete_strategy": self._meta_get("delete_strategy")
            or DEFAULT_POLICY["delete_strategy"],
        }

    def configure(self, conflict_strategy=None, delete_strategy=None,
                  tombstone_days=None):
        """Apply StartOS config knobs into the vault (ignoring unset/invalid)."""
        with self._lock:
            if conflict_strategy in ("preserve", "lww"):
                self._meta_set("conflict_strategy", conflict_strategy)
            if delete_strategy in ("edit-wins", "delete-wins"):
                self._meta_set("delete_strategy", delete_strategy)
            if tombstone_days not in (None, ""):
                try:
                    self._meta_set("tombstone_days", str(int(tombstone_days)))
                except (TypeError, ValueError):
                    pass
            self._conn.commit()

    def tombstone_days(self):
        try:
            return int(self._meta_get("tombstone_days"))
        except (TypeError, ValueError):
            return 90

    def compact_tombstones(self):
        """Purge tombstones (and their change-log rows) older than the retention
        window so long-deleted entries don't accumulate forever. The tradeoff:
        a device offline longer than the window won't learn of those deletions."""
        days = self.tombstone_days()
        if days <= 0:
            return 0
        cutoff = int(self._now()) - days * 86400 * 1000
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, hlc FROM entries WHERE deleted=1"
            ).fetchall()
            old = []
            for r in rows:
                try:
                    if parse_hlc(r["hlc"])[0] < cutoff:
                        old.append(r["id"])
                except Exception:
                    pass
            for eid in old:
                self._conn.execute("DELETE FROM entries WHERE id=?", (eid,))
                self._conn.execute("DELETE FROM change_log WHERE entry_id=?", (eid,))
            self._conn.commit()
            return len(old)

    def _row_to_entry(self, row):
        if row is None:
            return None
        return {
            "id": row["id"],
            "key": row["key"],
            "value": json.loads(row["value"]),
            "order": row["ord"],
            "created": row["created"],
            "rev": row["rev"],
            "hlc": row["hlc"],
            "deleted": bool(row["deleted"]),
            "conflict": json.loads(row["conflict"]) if row["conflict"] else None,
        }

    def _entry_wire(self, row):
        """Full entry record as sent to a sync client."""
        return self._row_to_entry(row)

    def _get_row(self, eid):
        return self._conn.execute(
            "SELECT * FROM entries WHERE id=?", (eid,)
        ).fetchone()

    def _next_order(self):
        row = self._conn.execute("SELECT MAX(ord) AS m FROM entries").fetchone()
        return (int(row["m"]) if row["m"] is not None else 0) + ORDER_GAP

    def _dedupe_key(self, key, exclude_id):
        """Return `key`, or `key (N)` if a different live entry already uses it."""
        taken = {
            r["key"]
            for r in self._conn.execute(
                "SELECT key FROM entries WHERE deleted=0 AND id<>?", (exclude_id,)
            ).fetchall()
        }
        if key not in taken:
            return key
        n = 1
        while f"{key} ({n})" in taken:
            n += 1
        return f"{key} ({n})"

    def _write_content(self, eid, content, rev, now):
        order = content.get("order")
        if order is None:
            order = self._next_order()
        # Persist the AUTHOR's hlc (from the change), which is the conflict
        # tiebreak basis. Fall back to a server stamp only if none was supplied
        # (e.g. the whole-blob web path, where the server is the author).
        hlc = content.get("hlc") or self._clock.now()
        self._conn.execute(
            "INSERT INTO entries (id,key,value,ord,created,rev,hlc,deleted,conflict) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET key=excluded.key, value=excluded.value, "
            "ord=excluded.ord, rev=excluded.rev, hlc=excluded.hlc, "
            "deleted=excluded.deleted, conflict=excluded.conflict",
            (
                eid,
                content.get("key", ""),
                json.dumps(content.get("value")),
                order,
                int(content.get("created") or now),
                rev,
                hlc,
                1 if content.get("deleted") else 0,
                json.dumps(content["conflict"]) if content.get("conflict") else None,
            ),
        )
        self._conn.execute(
            "INSERT INTO change_log (entry_id,rev,hlc,ts) VALUES (?,?,?,?)",
            (eid, rev, hlc, now),
        )

    def apply_sync(self, since, changes, device_hlc=None):
        """Push `changes` (resolving conflicts) and pull everything after `since`.

        One transaction. Returns {applied, changes, new_since, server_hlc}.
        """
        with self._lock:
            now = int(self._now())
            policy = self.policy()
            if device_hlc:
                self._clock.update(device_hlc)
            applied = []
            pushed_ids = []
            for change in changes or []:
                eid = change.get("id")
                if not eid:
                    eid = str(uuid.uuid4())
                    change = dict(change, id=eid)
                pushed_ids.append(eid)
                if change.get("hlc"):
                    self._clock.update(change["hlc"])  # keep server clock ahead
                row = self._get_row(eid)
                current = self._row_to_entry(row)
                plan = resolve(current, change, policy)
                result = plan["result"]
                new_rev = row["rev"] if row else 0

                canon = plan["canonical"]
                if canon is not None:
                    # Duplicate-key: only when a brand-new live entry appears.
                    if current is None and not canon.get("deleted"):
                        deduped = self._dedupe_key(canon.get("key", ""), eid)
                        if deduped != canon.get("key"):
                            canon = dict(canon, key=deduped)
                            result = "duplicate"
                    if canon.get("order") is None and row is not None:
                        canon = dict(canon, order=row["ord"])
                    if canon.get("created") is None and row is not None:
                        canon = dict(canon, created=row["created"])
                    new_rev = (row["rev"] + 1) if row else 1
                    self._write_content(eid, canon, new_rev, now)

                if plan["sibling"] is not None:
                    sib = plan["sibling"]
                    sib_id = str(uuid.uuid4())
                    sib_key = self._dedupe_key(
                        f"{sib.get('key', '')} (conflict)", sib_id
                    )
                    sib_content = dict(
                        sib,
                        key=sib_key,
                        order=self._next_order(),
                        created=now,
                        conflict={"of": eid},
                    )
                    self._write_content(sib_id, sib_content, 1, now)

                applied.append({"id": eid, "rev": new_rev, "result": result})

            new_since, out = self._changes_since_locked(since)
            # Always echo the current state of every pushed id, even when the
            # push lost its conflict and produced no change-log row — otherwise a
            # losing client would keep its (now stale) optimistic value.
            out_ids = {e["id"] for e in out}
            for eid in pushed_ids:
                if eid not in out_ids:
                    row = self._get_row(eid)
                    if row is not None:
                        out.append(self._entry_wire(row))
                        out_ids.add(eid)
            self._conn.commit()
            return {
                "applied": applied,
                "changes": out,
                "new_since": new_since,
                "server_hlc": self._clock.now(),
            }

    def _changes_since_locked(self, since):
        since = int(since or 0)
        rows_seq = self._conn.execute(
            "SELECT entry_id, MAX(seq) AS seq FROM change_log WHERE seq>? "
            "GROUP BY entry_id ORDER BY seq ASC",
            (since,),
        ).fetchall()
        gmax = self._conn.execute(
            "SELECT COALESCE(MAX(seq),0) AS m FROM change_log"
        ).fetchone()["m"]
        out = []
        for rs in rows_seq:
            row = self._get_row(rs["entry_id"])
            if row is not None:
                out.append(self._entry_wire(row))
        return gmax, out

    def changes_since(self, since):
        with self._lock:
            new_since, out = self._changes_since_locked(since)
            return {"changes": out, "new_since": new_since}

    def entries_for_scope(self, prefix=None):
        """Live entries whose key starts with `prefix`, with the crypto key.

        The key NAMES are stored in the clear and only the values are
        ciphertext, so scoping happens here without the server ever needing to
        decrypt anything. That matters: adding AES to a standard-library server
        to filter on a name it can already read would be a dependency bought
        for nothing.
        """
        with self._lock:
            if prefix:
                # substr, not LIKE: SQLite's LIKE is case-INSENSITIVE for ASCII,
                # so a key scoped to "HERMES_" also matched "hermes_lower". A
                # scope that leaks across case is not a scope. This also needs
                # no % or _ escaping, which LIKE did and which is easy to get
                # subtly wrong.
                rows = self._conn.execute(
                    "SELECT key, value FROM entries WHERE deleted=0 "
                    "AND substr(key, 1, ?) = ? ORDER BY key",
                    (len(prefix), prefix)).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT key, value FROM entries WHERE deleted=0 ORDER BY key"
                ).fetchall()
            return {
                "entries": [{"key": r["key"], "value": json.loads(r["value"])}
                            for r in rows],
                "cryptoKey": self.get_crypto_key(),
                "prefix": prefix or "",
            }

    def set_key_scope(self, kid, scope_prefix=None, read_only=None):
        with self._lock:
            if scope_prefix is not None:
                self._conn.execute("UPDATE api_keys SET scope_prefix=? WHERE id=?",
                                   (scope_prefix or None, kid))
            if read_only is not None:
                self._conn.execute("UPDATE api_keys SET read_only=? WHERE id=?",
                                   (1 if read_only else 0, kid))
            self._conn.commit()

    def bootstrap(self):
        with self._lock:
            return {
                "server_id": self.server_id,
                "e2e_key_mode": "paired",
                "vault_created": int(self._meta_get("vault_created") or 0),
                "server_hlc": self._clock.now(),
                "protocol_version": 1,
            }

    # ── API keys / device enrollment ───────────────────────────────
    def _sweep_expired_locked(self):
        now = int(self._now())
        self._conn.execute(
            "DELETE FROM api_keys WHERE status='pending' AND "
            "(enroll_expires IS NULL OR enroll_expires < ?)",
            (now,),
        )

    def create_pending_key(self, label):
        """Start enrollment: a pending row + a one-time code (returned raw once)."""
        with self._lock:
            now = int(self._now())
            kid = secrets.token_hex(6)
            code = "e_" + secrets.token_urlsafe(24)
            exp = now + ENROLL_TTL_MS
            self._conn.execute(
                "INSERT INTO api_keys (id,label,status,enroll_hash,enroll_expires,created) "
                "VALUES (?,?,'pending',?,?,?)",
                (kid, label or "", _sha256(code), exp, now),
            )
            self._conn.commit()
            return {"id": kid, "label": label or "", "status": "pending",
                    "created": now, "enroll_code": code, "enroll_expires": exp}

    def list_keys(self):
        with self._lock:
            self._sweep_expired_locked()
            self._conn.commit()
            rows = self._conn.execute(
                "SELECT id,label,status,key_prefix,created,last_used,last_transport,"
                "scope_prefix,read_only "
                "FROM api_keys WHERE status!='revoked' ORDER BY created DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def claim_enrollment(self, code, label=None):
        """Exchange a valid one-time code for the long-term device secret."""
        with self._lock:
            now = int(self._now())
            self._sweep_expired_locked()
            row = self._conn.execute(
                "SELECT * FROM api_keys WHERE enroll_hash=? AND status='pending'",
                (_sha256(code or ""),),
            ).fetchone()
            if row is None or (row["enroll_expires"] or 0) < now:
                self._conn.commit()
                return None
            kid = row["id"]
            secret = f"kvc_{kid}_{secrets.token_urlsafe(32)}"
            self._conn.execute(
                "UPDATE api_keys SET status='active', key_hash=?, key_prefix=?, "
                "enroll_hash=NULL, enroll_expires=NULL, label=COALESCE(?,label) WHERE id=?",
                (_sha256(secret), f"kvc_{kid}", label, kid),
            )
            self._conn.commit()
            return {"api_key": secret, "key_id": kid}

    def verify_api_key(self, secret, transport=None):
        """Return the key RECORD for a valid active secret (constant-time), else None.

        It used to return the id alone. Every caller that enforces a scope needs
        the scope itself, and looking it up separately would mean a second query
        that could disagree with the one that authenticated.
        """
        if not secret:
            return None
        with self._lock:
            h = _sha256(secret)
            rows = self._conn.execute(
                "SELECT id,key_hash,scope_prefix,read_only,label "
                "FROM api_keys WHERE status='active'"
            ).fetchall()
            for r in rows:
                if r["key_hash"] and hmac.compare_digest(h, r["key_hash"]):
                    self._conn.execute(
                        "UPDATE api_keys SET last_used=?, last_transport=? WHERE id=?",
                        (int(self._now()), transport, r["id"]),
                    )
                    self._conn.commit()
                    return {"id": r["id"], "label": r["label"],
                            "scope_prefix": r["scope_prefix"],
                            "read_only": bool(r["read_only"])}
            return None

    def revoke_key(self, kid):
        with self._lock:
            cur = self._conn.execute(
                "UPDATE api_keys SET status='revoked', revoked_at=?, enroll_hash=NULL, "
                "enroll_expires=NULL WHERE id=? AND status!='revoked'",
                (int(self._now()), kid),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def rename_key(self, kid, label):
        with self._lock:
            cur = self._conn.execute(
                "UPDATE api_keys SET label=? WHERE id=? AND status!='revoked'",
                (label, kid),
            )
            self._conn.commit()
            return cur.rowcount > 0

    # ── migration from legacy store.json ───────────────────────────
    def is_empty(self):
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM entries").fetchone()
            return row["n"] == 0 and self.get_crypto_key() is None

    def migrate_from_json(self, json_path):
        """Seed the DB from a legacy store.json. Reuses existing ids, seeds each
        entry's HLC from its `modified` time, and preserves array order."""
        path = Path(json_path)
        data = json.loads(path.read_text())
        entries = data.get("entries", []) or []
        crypto = data.get("cryptoKey")
        node = self.server_id[:4]
        with self._lock:
            now = int(self._now())
            for idx, e in enumerate(entries):
                eid = e.get("id") or str(uuid.uuid4())
                modified = int(e.get("modified") or e.get("created") or now)
                created = int(e.get("created") or modified)
                hlc = f"{modified}:0:{node}"
                self._conn.execute(
                    "INSERT INTO entries (id,key,value,ord,created,rev,hlc,deleted,conflict) "
                    "VALUES (?,?,?,?,?,1,?,0,NULL) "
                    "ON CONFLICT(id) DO NOTHING",
                    (
                        eid,
                        e.get("key", ""),
                        json.dumps(e.get("value")),
                        (idx + 1) * ORDER_GAP,
                        created,
                        hlc,
                    ),
                )
                self._conn.execute(
                    "INSERT INTO change_log (entry_id,rev,hlc,ts) VALUES (?,1,?,?)",
                    (eid, hlc, now),
                )
            if crypto is not None:
                self._meta_set("crypto_key", json.dumps(crypto))
            self._conn.commit()
        return len(entries)

    def close(self):
        with self._lock:
            self._conn.close()
