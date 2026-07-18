#!/usr/bin/env python3
"""Conflict-resolution tests — the correctness core of the sync protocol.

Covers every reconciliation case at the pure-function level (resolve) plus the
store-level behaviours built on it: conflict siblings, duplicate-key suffixing,
delta pull, idempotent re-push, and order-independent convergence.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sync_engine import resolve  # noqa: E402
from store import Store  # noqa: E402

PRESERVE = {"conflict_strategy": "preserve", "delete_strategy": "edit-wins"}
LWW = {"conflict_strategy": "lww", "delete_strategy": "edit-wins"}
DELETE_WINS = {"conflict_strategy": "preserve", "delete_strategy": "delete-wins"}

LO = "100:0:x"   # older HLC
HI = "200:0:y"   # newer HLC


def entry(rev=1, key="K", ct=1, hlc=LO, deleted=False):
    return {"id": "a", "key": key, "value": {"ct": ct}, "order": 1024,
            "created": 1, "rev": rev, "hlc": hlc, "deleted": deleted, "conflict": None}


def change(key="K", ct=1, base_rev=1, hlc=LO, deleted=False):
    return {"id": "a", "key": key, "value": {"ct": ct},
            "base_rev": base_rev, "hlc": hlc, "deleted": deleted}


class ResolvePureTests(unittest.TestCase):
    def test_new_entry(self):
        p = resolve(None, change(base_rev=0), PRESERVE)
        self.assertEqual(p["result"], "ok")
        self.assertIsNotNone(p["canonical"])
        self.assertIsNone(p["sibling"])

    def test_fast_forward_edit(self):
        p = resolve(entry(rev=1), change(ct=9, base_rev=1), PRESERVE)
        self.assertEqual(p["result"], "ok")
        self.assertEqual(p["canonical"]["value"], {"ct": 9})

    def test_fast_forward_delete(self):
        p = resolve(entry(rev=1), change(base_rev=1, deleted=True), PRESERVE)
        self.assertEqual(p["result"], "ok")
        self.assertTrue(p["canonical"]["deleted"])

    def test_uu_preserve_incoming_newer(self):
        p = resolve(entry(rev=5, ct=1, hlc=LO), change(ct=2, base_rev=1, hlc=HI), PRESERVE)
        self.assertEqual(p["result"], "conflict")
        self.assertEqual(p["canonical"]["value"], {"ct": 2})   # incoming wins
        self.assertEqual(p["sibling"]["value"], {"ct": 1})     # current preserved

    def test_uu_preserve_current_newer(self):
        p = resolve(entry(rev=5, ct=1, hlc=HI), change(ct=2, base_rev=1, hlc=LO), PRESERVE)
        self.assertEqual(p["result"], "conflict")
        self.assertIsNone(p["canonical"])                      # current kept
        self.assertEqual(p["sibling"]["value"], {"ct": 2})     # incoming preserved

    def test_uu_lww_incoming_newer(self):
        p = resolve(entry(rev=5, ct=1, hlc=LO), change(ct=2, base_rev=1, hlc=HI), LWW)
        self.assertEqual(p["canonical"]["value"], {"ct": 2})
        self.assertIsNone(p["sibling"])

    def test_uu_lww_current_newer(self):
        p = resolve(entry(rev=5, ct=1, hlc=HI), change(ct=2, base_rev=1, hlc=LO), LWW)
        self.assertIsNone(p["canonical"])
        self.assertIsNone(p["sibling"])

    def test_uu_identical_content_is_no_conflict(self):
        p = resolve(entry(rev=5, key="K", ct=7), change(key="K", ct=7, base_rev=1), PRESERVE)
        self.assertEqual(p["result"], "ok")
        self.assertIsNone(p["canonical"])
        self.assertIsNone(p["sibling"])

    def test_delete_vs_edit_edit_wins(self):
        # incoming delete vs live current edit -> edit survives, flagged
        p = resolve(entry(rev=5, deleted=False), change(base_rev=1, deleted=True), PRESERVE)
        self.assertEqual(p["result"], "conflict")
        self.assertFalse(p["canonical"]["deleted"])
        self.assertEqual(p["canonical"]["conflict"], {"reason": "delete-vs-edit"})

    def test_tombstone_vs_edit_edit_wins_resurrects(self):
        p = resolve(entry(rev=5, deleted=True), change(ct=3, base_rev=1, deleted=False), PRESERVE)
        self.assertEqual(p["result"], "conflict")
        self.assertFalse(p["canonical"]["deleted"])
        self.assertEqual(p["canonical"]["value"], {"ct": 3})
        self.assertEqual(p["canonical"]["conflict"], {"reason": "resurrect"})

    def test_delete_vs_edit_delete_wins(self):
        p = resolve(entry(rev=5, deleted=False), change(base_rev=1, deleted=True), DELETE_WINS)
        self.assertTrue(p["canonical"]["deleted"])

    def test_tombstone_vs_edit_delete_wins_keeps_tombstone(self):
        p = resolve(entry(rev=5, deleted=True), change(base_rev=1, deleted=False), DELETE_WINS)
        self.assertIsNone(p["canonical"])

    def test_both_deleted(self):
        p = resolve(entry(rev=5, deleted=True), change(base_rev=1, deleted=True), PRESERVE)
        self.assertEqual(p["result"], "ok")
        self.assertIsNone(p["sibling"])


def sync_new(store, eid, key, ct, hlc):
    return store.apply_sync(0, [{"id": eid, "key": key, "value": {"ct": ct},
                                 "base_rev": 0, "hlc": hlc, "deleted": False}])


class StoreSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "kvc.db"))

    def tearDown(self):
        self.store.close()

    def _live(self):
        # Full entry records (incl. conflict/deleted), filtered to live rows.
        return {
            e["id"]: e
            for e in self.store.changes_since(0)["changes"]
            if not e["deleted"]
        }

    def test_duplicate_key_suffixed(self):
        r = self.store.apply_sync(0, [
            {"id": "a", "key": "X", "value": {"ct": 1}, "base_rev": 0, "hlc": LO},
            {"id": "b", "key": "X", "value": {"ct": 2}, "base_rev": 0, "hlc": HI},
        ])
        results = {x["id"]: x["result"] for x in r["applied"]}
        self.assertEqual(results["a"], "ok")
        self.assertEqual(results["b"], "duplicate")
        keys = sorted(e["key"] for e in self.store.load_all()["entries"])
        self.assertEqual(keys, ["X", "X (1)"])

    def test_update_update_creates_sibling(self):
        sync_new(self.store, "a", "K", 1, LO)  # rev 1
        # two concurrent edits, both based on rev 1
        self.store.apply_sync(0, [{"id": "a", "key": "K", "value": {"ct": 10},
                                   "base_rev": 1, "hlc": LO}])
        self.store.apply_sync(0, [{"id": "a", "key": "K", "value": {"ct": 20},
                                   "base_rev": 1, "hlc": HI}])
        live = self._live()
        winners = [e for e in live.values() if e["conflict"] is None]
        siblings = [e for e in live.values() if e["conflict"] is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0]["value"], {"ct": 20})          # HI won
        self.assertEqual(len(siblings), 1)
        self.assertEqual(siblings[0]["value"], {"ct": 10})         # LO preserved
        self.assertEqual(siblings[0]["conflict"], {"of": "a"})
        self.assertTrue(siblings[0]["key"].endswith("(conflict)"))

    def test_delta_pull_is_incremental(self):
        r1 = sync_new(self.store, "a", "K", 1, LO)
        since = r1["new_since"]
        r2 = self.store.changes_since(since)
        self.assertEqual(r2["changes"], [])                        # nothing new yet
        sync_new(self.store, "b", "K2", 2, HI)
        r3 = self.store.changes_since(since)
        self.assertEqual([e["id"] for e in r3["changes"]], ["b"])  # only the new one

    def test_idempotent_re_push(self):
        c = {"id": "a", "key": "K", "value": {"ct": 1}, "base_rev": 0, "hlc": LO}
        self.store.apply_sync(0, [c])
        before = self.store.load_all()["entries"]
        self.store.apply_sync(0, [c])   # exact same change again (lost-ack replay)
        after = self.store.load_all()["entries"]
        self.assertEqual(len(after), 1, "re-push must not duplicate")
        self.assertEqual(before, after)

    def test_edit_wins_resurrect_end_to_end(self):
        r1 = sync_new(self.store, "a", "K", 1, LO)          # rev 1
        self.store.apply_sync(0, [{"id": "a", "key": "K", "value": {"ct": 1},
                                   "base_rev": 1, "hlc": HI, "deleted": True}])  # rev 2 tombstone
        self.assertEqual(self._live(), {})                  # gone from live view
        # a device that never saw the delete edits from rev 1
        self.store.apply_sync(0, [{"id": "a", "key": "K", "value": {"ct": 99},
                                   "base_rev": 1, "hlc": "300:0:z"}])
        live = self._live()
        self.assertIn("a", live)                            # resurrected
        self.assertEqual(live["a"]["value"], {"ct": 99})
        self.assertEqual(live["a"]["conflict"], {"reason": "resurrect"})

    def test_losing_push_echoes_current_winner(self):
        # winner writes a=9 (rev1, seq1)
        self.store.apply_sync(0, [{"id": "a", "key": "K", "value": {"ct": 9},
                                   "base_rev": 0, "hlc": "500:0:w"}])
        # a stale client (since already at seq1) pushes a losing edit
        resp = self.store.apply_sync(1, [{"id": "a", "key": "K", "value": {"ct": 1},
                                          "base_rev": 0, "hlc": "100:0:l"}])
        by_id = {e["id"]: e for e in resp["changes"]}
        self.assertIn("a", by_id, "winner must be echoed back to the losing pusher")
        self.assertEqual(by_id["a"]["value"], {"ct": 9})
        sibs = [e for e in resp["changes"] if e["conflict"]]
        self.assertEqual(len(sibs), 1)
        self.assertEqual(sibs[0]["value"], {"ct": 1}, "loser preserved as sibling")

    def test_convergence_independent_of_order(self):
        def build():
            s = Store(os.path.join(tempfile.mkdtemp(), "kvc.db"))
            sync_new(s, "a", "K", 0, LO)  # shared starting point, rev 1
            return s
        x = {"id": "a", "key": "K", "value": {"ct": 1}, "base_rev": 1, "hlc": LO}
        y = {"id": "a", "key": "K", "value": {"ct": 2}, "base_rev": 1, "hlc": HI}
        s1, s2 = build(), build()
        s1.apply_sync(0, [x]); s1.apply_sync(0, [y])   # order X then Y
        s2.apply_sync(0, [y]); s2.apply_sync(0, [x])   # order Y then X

        def summary(s):
            live = [e for e in s.changes_since(0)["changes"] if not e["deleted"]]
            winner = [e["value"] for e in live if e["conflict"] is None]
            losers = sorted(str(e["value"]) for e in live if e["conflict"])
            return winner, losers

        w1, l1 = summary(s1)
        w2, l2 = summary(s2)
        self.assertEqual(w1, [{"ct": 2}], "higher-HLC value must win")
        self.assertEqual(w1, w2, "winner identical regardless of apply order")
        self.assertEqual(l1, l2, "preserved losers identical regardless of order")
        s1.close(); s2.close()


if __name__ == "__main__":
    unittest.main()
