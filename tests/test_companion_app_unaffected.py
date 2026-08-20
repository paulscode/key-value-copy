#!/usr/bin/env python3
"""The companion app's flow must be untouched by device-key scoping.

The app (key-value-copy-android) pairs, bootstraps, pulls the change feed and
pushes changes. Scoping added refusals to two of those, so this walks the app's
exact endpoint sequence with an ORDINARY unscoped key and asserts nothing
changed for it. The scoped-key tests live next door and assert the opposite.
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

# Exactly what app/src/main/java/com/paulscode/keyvaluecopy/net/Transport.kt calls.
APP_ENDPOINTS = ("/api/enrollment", "/api/v1/bootstrap",
                 "/api/v1/changes?since=0", "/api/v1/pair", "/api/v1/sync")


class CompanionAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.store = Store(os.path.join(cls.tmp, "kvc.db"))
        cls.store._conn.execute(
            "INSERT INTO entries(id,key,value,ord,created,hlc) VALUES (?,?,?,?,?,'')",
            ("E1", "Personal", json.dumps({"iv": [1], "ct": "x"}), 1,
             int(time.time() * 1000)))
        cls.store.set_crypto_key({"kty": "oct", "k": "vaultkey"})
        cls.store._conn.commit()
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

    def call(self, path, key=None, body=None):
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            method="POST" if body is not None else "GET", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def pair_like_the_app(self):
        """The app's real pairing: a code from the UI flow, claimed over /pair.

        The UI's add-device call sets no scope, which is what makes the app's
        key unscoped and therefore unaffected by any of this.
        """
        pending = self.store.create_pending_key("Pixel 8")
        status, res = self.call("/api/v1/pair", body={
            "enroll_code": pending["enroll_code"], "label": "Pixel 8"})
        self.assertEqual(status, 200)
        return res

    def test_pairing_still_returns_what_the_app_needs(self):
        res = self.pair_like_the_app()
        for field in ("api_key", "key_id", "e2e_key"):
            self.assertIn(field, res, f"pairing lost {field}")
        self.assertEqual(res["e2e_key"]["k"], "vaultkey")

    def test_a_paired_key_is_unscoped(self):
        res = self.pair_like_the_app()
        rec = self.store.verify_api_key(res["api_key"])
        self.assertIsNone(rec["scope_prefix"])
        self.assertFalse(rec["read_only"])

    def test_every_endpoint_the_app_calls_still_answers(self):
        key = self.pair_like_the_app()["api_key"]
        for path in APP_ENDPOINTS:
            if path == "/api/v1/pair":
                continue          # one-time by design; covered above
            body = {"changes": []} if path == "/api/v1/sync" else None
            status, _ = self.call(path, key=key, body=body)
            self.assertEqual(status, 200, f"{path} answered {status}")

    def test_the_app_can_still_read_the_whole_change_feed(self):
        key = self.pair_like_the_app()["api_key"]
        status, body = self.call("/api/v1/changes?since=0", key=key)
        self.assertEqual(status, 200)
        self.assertIn("changes", body)

    def test_the_app_can_still_push(self):
        key = self.pair_like_the_app()["api_key"]
        status, _ = self.call("/api/v1/sync", key=key, body={"changes": []})
        self.assertEqual(status, 200)

    def test_extra_response_fields_are_additive_only(self):
        """list_keys gained two columns. The app ignores unknown keys
        (ignoreUnknownKeys = true in Transport.kt), and it does not call this
        endpoint at all, but nothing it DOES call should have lost a field."""
        res = self.pair_like_the_app()
        self.assertEqual(set(res) >= {"api_key", "key_id", "e2e_key", "ca_pem"}, True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
