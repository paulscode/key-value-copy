#!/usr/bin/env python3
"""A scoped device key over real HTTP: every path it can reach must respect it.

Scoping one endpoint while another still returns the whole store is theatre, so
this drives the actual server and tries the bypasses rather than trusting that
the new endpoint is the only way in.
"""

import http.server
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server as S  # noqa: E402
from store import Store  # noqa: E402


class ScopedHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.store = Store(os.path.join(cls.tmp, "kvc.db"))
        for key in ("HERMES_CARD", "PERSONAL_BANK"):
            cls.store._conn.execute(
                "INSERT INTO entries(id,key,value,ord,created,hlc) VALUES (?,?,?,?,?,'')",
                (key, key, json.dumps({"iv": [1], "ct": "x"}), 1,
                 int(time.time() * 1000)))
        cls.store.set_crypto_key({"kty": "oct", "k": "testkey"})
        cls.store._conn.commit()

        pending = cls.store.create_pending_key("Hermes agent")
        claimed = cls.store.claim_enrollment(pending["enroll_code"])
        cls.store.set_key_scope(claimed["key_id"], scope_prefix="HERMES_",
                                read_only=True)
        cls.scoped = claimed["api_key"]
        cls.full = cls.store.claim_enrollment(
            cls.store.create_pending_key("Phone")["enroll_code"])["api_key"]

        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), S.KVCHandler)
        cls.httpd.daemon_threads = True
        cls.httpd.store = cls.store
        cls.httpd.api_enabled = True
        cls.httpd.ca_sha256 = None
        cls.httpd.ca_pem = None
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.store.close()

    def get(self, path, key):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     headers={"Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def post(self, path, key, body):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=json.dumps(body).encode(),
            method="POST",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    # ── the endpoint itself ──────────────────────────────────────────
    def test_scoped_key_reads_only_its_prefix(self):
        status, body = self.get("/api/v1/env", self.scoped)
        self.assertEqual(status, 200)
        self.assertEqual([e["key"] for e in body["entries"]], ["HERMES_CARD"])

    def test_it_returns_the_crypto_key_so_the_caller_can_decrypt(self):
        _, body = self.get("/api/v1/env", self.scoped)
        self.assertEqual(body["cryptoKey"]["k"], "testkey")

    def test_values_are_still_ciphertext(self):
        _, body = self.get("/api/v1/env", self.scoped)
        self.assertIn("ct", body["entries"][0]["value"])

    def test_a_key_may_narrow_its_scope(self):
        status, body = self.get("/api/v1/env?prefix=HERMES_CARD", self.scoped)
        self.assertEqual(status, 200)
        self.assertEqual(len(body["entries"]), 1)

    def test_a_key_may_not_widen_its_scope(self):
        status, body = self.get("/api/v1/env?prefix=PERSONAL", self.scoped)
        self.assertEqual(status, 403)

    def test_an_unscoped_key_may_not_use_env_at_all(self):
        # Otherwise this becomes a second way to dump the whole store, which is
        # the thing scoping exists to prevent.
        status, _ = self.get("/api/v1/env", self.full)
        self.assertEqual(status, 403)

    # ── the bypasses ─────────────────────────────────────────────────
    def test_scoped_key_cannot_read_the_change_feed(self):
        # /api/v1/changes returns the whole store by design. Before this, a
        # scoped key could simply ask it instead.
        status, body = self.get("/api/v1/changes?since=0", self.scoped)
        self.assertEqual(status, 403)
        self.assertIn("/api/v1/env", json.dumps(body))

    def test_an_unscoped_key_still_syncs_normally(self):
        status, _ = self.get("/api/v1/changes?since=0", self.full)
        self.assertEqual(status, 200)

    def test_scoped_key_cannot_write(self):
        status, _ = self.post("/api/v1/sync", self.scoped, {"changes": []})
        self.assertEqual(status, 403)

    def test_scoped_key_cannot_read_api_data(self):
        # /api/data is session-only, so a device key of any kind is refused.
        status, _ = self.get("/api/data", self.scoped)
        self.assertIn(status, (401, 403))

    def test_no_key_at_all_is_refused(self):
        status, _ = self.get("/api/v1/env", "kvc_not_a_real_key")
        self.assertIn(status, (401, 429))


if __name__ == "__main__":
    unittest.main(verbosity=2)
