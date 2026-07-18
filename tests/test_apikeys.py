#!/usr/bin/env python3
"""API-key / device-enrollment lifecycle tests."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from store import Store  # noqa: E402


class Clock:
    def __init__(self, t=1_000_000): self.t = t
    def __call__(self): return self.t


class ApiKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.clk = Clock()
        self.store = Store(os.path.join(self.tmp, "kvc.db"), now_ms=self.clk)

    def tearDown(self):
        self.store.close()

    def test_pending_then_claim_activates(self):
        p = self.store.create_pending_key("Pixel 8")
        self.assertEqual(p["status"], "pending")
        self.assertTrue(p["enroll_code"].startswith("e_"))
        lst = self.store.list_keys()
        self.assertEqual(lst[0]["status"], "pending")
        self.assertIsNone(lst[0]["key_prefix"])

        res = self.store.claim_enrollment(p["enroll_code"])
        self.assertIsNotNone(res)
        self.assertTrue(res["api_key"].startswith(f"kvc_{p['id']}_"))
        lst = self.store.list_keys()
        self.assertEqual(lst[0]["status"], "active")
        self.assertEqual(lst[0]["key_prefix"], f"kvc_{p['id']}")

    def test_verify_only_active_secret(self):
        p = self.store.create_pending_key("dev")
        # code not yet claimed -> no usable secret
        self.assertIsNone(self.store.verify_api_key("kvc_x_bogus"))
        secret = self.store.claim_enrollment(p["enroll_code"])["api_key"]
        self.assertEqual(self.store.verify_api_key(secret, transport="lan"), p["id"])
        self.assertIsNone(self.store.verify_api_key(secret + "tamper"))
        self.assertIsNone(self.store.verify_api_key(""))
        # last_used/transport recorded
        self.assertEqual(self.store.list_keys()[0]["last_transport"], "lan")

    def test_enrollment_code_is_single_use(self):
        p = self.store.create_pending_key("dev")
        self.assertIsNotNone(self.store.claim_enrollment(p["enroll_code"]))
        self.assertIsNone(self.store.claim_enrollment(p["enroll_code"]),
                          "a code must not be claimable twice")

    def test_enrollment_code_expires(self):
        p = self.store.create_pending_key("dev")
        self.clk.t += 6 * 60 * 1000  # 6 min > 5 min TTL
        self.assertIsNone(self.store.claim_enrollment(p["enroll_code"]))
        # expired pending row is swept from the listing
        self.assertEqual(self.store.list_keys(), [])

    def test_revoke_kills_active_key(self):
        p = self.store.create_pending_key("dev")
        secret = self.store.claim_enrollment(p["enroll_code"])["api_key"]
        self.assertTrue(self.store.revoke_key(p["id"]))
        self.assertIsNone(self.store.verify_api_key(secret),
                          "revoked key must not authenticate")
        self.assertEqual(self.store.list_keys(), [],
                         "revoked keys are hidden from the active listing")

    def test_revoke_kills_unclaimed_code(self):
        p = self.store.create_pending_key("dev")
        self.assertTrue(self.store.revoke_key(p["id"]))
        self.assertIsNone(self.store.claim_enrollment(p["enroll_code"]),
                          "revoking a pending device invalidates its code")

    def test_rename(self):
        p = self.store.create_pending_key("old")
        self.assertTrue(self.store.rename_key(p["id"], "new name"))
        self.assertEqual(self.store.list_keys()[0]["label"], "new name")


if __name__ == "__main__":
    unittest.main()
