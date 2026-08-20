#!/usr/bin/env python3
"""A device key scoped to a prefix must not be able to read past it.

The reason this exists: one vault holds personal, business and machine values
side by side. A key that can dump everything is a poor trade for the
convenience of a machine reading its own two secrets, so the scope has to hold
on every path a device key can reach, not just the newest one.
"""

import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from store import Store  # noqa: E402


class ScopedKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "kvc.db"))
        for key in ("HERMES_CARD", "HERMES_TOKEN", "PERSONAL_BANK",
                    "hermes_lower", "HERMESX"):
            self.store._conn.execute(
                "INSERT INTO entries(id,key,value,ord,created,hlc) VALUES (?,?,?,?,?,'')",
                (key, key, json.dumps({"iv": [1], "ct": "x"}), 1,
                 int(time.time() * 1000)))
        self.store._conn.commit()

    def tearDown(self):
        self.store.close()

    def keys_for(self, prefix):
        return [e["key"] for e in self.store.entries_for_scope(prefix)["entries"]]

    def test_scope_returns_only_matching_entries(self):
        self.assertEqual(self.keys_for("HERMES_"), ["HERMES_CARD", "HERMES_TOKEN"])

    def test_scope_is_case_sensitive(self):
        # SQLite's LIKE is case-insensitive for ASCII, so the first version of
        # this matched `hermes_lower` from a HERMES_ scope. A scope that leaks
        # across case is not a scope.
        self.assertNotIn("hermes_lower", self.keys_for("HERMES_"))

    def test_underscore_is_not_a_wildcard(self):
        # Under LIKE, `_` matches any single character, so a HERMES_ scope would
        # also have matched HERMESX.
        self.assertNotIn("HERMESX", self.keys_for("HERMES_"))

    def test_no_scope_returns_everything(self):
        # Existing keys were all issued unscoped, so the column has to be
        # additive in behaviour as well as in schema.
        self.assertEqual(len(self.keys_for(None)), 5)

    def test_values_are_still_ciphertext(self):
        # The server filters on the key NAME, which is stored in the clear, and
        # never decrypts. If this ever returns plaintext, a dependency was added
        # that this design does not need.
        entry = self.store.entries_for_scope("HERMES_")["entries"][0]
        self.assertIn("iv", entry["value"])
        self.assertIn("ct", entry["value"])

    def test_crypto_key_is_returned_for_the_caller_to_decrypt(self):
        self.store.set_crypto_key({"kty": "oct", "k": "test"})
        self.assertEqual(
            self.store.entries_for_scope("HERMES_")["cryptoKey"]["k"], "test")

    def test_verify_api_key_reports_the_scope(self):
        pending = self.store.create_pending_key("Hermes agent")
        claimed = self.store.claim_enrollment(pending["enroll_code"])
        self.store.set_key_scope(claimed["key_id"], scope_prefix="HERMES_",
                                 read_only=True)
        rec = self.store.verify_api_key(claimed["api_key"])
        self.assertEqual(rec["scope_prefix"], "HERMES_")
        self.assertTrue(rec["read_only"])

    def test_an_unscoped_key_reports_no_scope(self):
        pending = self.store.create_pending_key("Phone")
        claimed = self.store.claim_enrollment(pending["enroll_code"])
        rec = self.store.verify_api_key(claimed["api_key"])
        self.assertIsNone(rec["scope_prefix"])
        self.assertFalse(rec["read_only"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
