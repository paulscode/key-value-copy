#!/usr/bin/env python3
"""M5 config knobs: policy configuration + tombstone compaction."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from store import Store  # noqa: E402


class Clock:
    def __init__(self, t=1_700_000_000_000): self.t = t
    def __call__(self): return self.t


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.clk = Clock()
        self.store = Store(os.path.join(self.tmp, "kvc.db"), now_ms=self.clk)

    def tearDown(self):
        self.store.close()

    def test_single_file_journal_for_backup(self):
        mode = self.store._conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode, "delete", "backups rely on a single kvc.db file")

    def test_configure_applies_and_defaults(self):
        self.assertEqual(self.store.policy(),
                         {"conflict_strategy": "preserve", "delete_strategy": "edit-wins"})
        self.assertEqual(self.store.tombstone_days(), 90)
        self.store.configure(conflict_strategy="lww", delete_strategy="delete-wins",
                             tombstone_days="30")
        self.assertEqual(self.store.policy(),
                         {"conflict_strategy": "lww", "delete_strategy": "delete-wins"})
        self.assertEqual(self.store.tombstone_days(), 30)

    def test_configure_ignores_invalid(self):
        self.store.configure(conflict_strategy="lww")
        self.store.configure(conflict_strategy="bogus", tombstone_days="not-a-number")
        self.assertEqual(self.store.policy()["conflict_strategy"], "lww")
        self.assertEqual(self.store.tombstone_days(), 90)

    def test_configure_changes_conflict_behaviour(self):
        # with lww, a losing edit is NOT preserved as a sibling
        self.store.configure(conflict_strategy="lww")
        self.store.apply_sync(0, [{"id": "a", "key": "K", "value": {"ct": 1},
                                   "base_rev": 0, "hlc": "100:0:x"}])
        self.store.apply_sync(0, [{"id": "a", "key": "K", "value": {"ct": 2},
                                   "base_rev": 0, "hlc": "200:0:y"}])  # conflict, higher wins
        live = [e for e in self.store.changes_since(0)["changes"] if not e["deleted"]]
        self.assertEqual(len(live), 1, "lww keeps no conflict sibling")
        self.assertEqual(live[0]["value"], {"ct": 2})

    def test_compaction_purges_old_tombstones_only(self):
        self.store.configure(tombstone_days="30")
        # entry deleted "recently"
        self.store.apply_sync(0, [{"id": "recent", "key": "R", "value": {"ct": 1},
                                   "base_rev": 0, "hlc": f"{self.clk.t}:0:x"}])
        # entry deleted "long ago"
        self.store.apply_sync(0, [{"id": "old", "key": "O", "value": {"ct": 1},
                                   "base_rev": 0, "hlc": f"{self.clk.t}:0:x"}])
        self.clk.t += 1000
        for eid in ("recent", "old"):
            self.store.apply_sync(0, [{"id": eid, "key": "x", "value": {"ct": 1},
                                       "base_rev": 1, "hlc": f"{self.clk.t}:0:x",
                                       "deleted": True}])
        # move the "old" tombstone's clock back so it's beyond the window;
        # instead advance global time past retention for both, then re-stamp recent
        self.clk.t += 40 * 86400 * 1000  # 40 days later: both are old now
        # re-touch 'recent' so its tombstone hlc is fresh
        self.store.apply_sync(0, [{"id": "recent", "key": "R2", "value": {"ct": 9},
                                   "base_rev": 2, "hlc": f"{self.clk.t}:0:x",
                                   "deleted": True}])
        purged = self.store.compact_tombstones()
        ids = {r["id"] for r in self.store._conn.execute("SELECT id FROM entries")}
        self.assertEqual(purged, 1)
        self.assertIn("recent", ids, "recently-deleted tombstone retained")
        self.assertNotIn("old", ids, "old tombstone purged")


if __name__ == "__main__":
    unittest.main()
