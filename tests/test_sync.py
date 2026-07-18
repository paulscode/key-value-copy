#!/usr/bin/env python3
"""Local unit tests for the M1 storage foundation — no Docker/StartOS needed.

Run from the repo root:  python3 -m unittest discover -s tests -v
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sync_engine import HLCClock, compare_hlc, parse_hlc  # noqa: E402
from store import Store  # noqa: E402


class FakeClock:
    """Deterministic, advanceable ms clock for tests."""

    def __init__(self, t=1_000_000):
        self.t = t

    def __call__(self):
        return self.t


class HLCTests(unittest.TestCase):
    def test_monotonic_when_wall_clock_stalls(self):
        clk = FakeClock()
        hlc = HLCClock("aa", clk)
        a = hlc.now()
        b = hlc.now()  # same wall time -> counter bumps
        c = hlc.now()
        self.assertEqual(compare_hlc(a, b), -1)
        self.assertEqual(compare_hlc(b, c), -1)
        self.assertEqual(parse_hlc(a)[1], 0)
        self.assertEqual(parse_hlc(b)[1], 1)

    def test_monotonic_when_wall_clock_goes_backwards(self):
        clk = FakeClock(5000)
        hlc = HLCClock("aa", clk)
        a = hlc.now()
        clk.t = 4000  # clock jumps backward
        b = hlc.now()
        self.assertEqual(compare_hlc(a, b), -1, "HLC must not regress")

    def test_advances_with_wall_clock(self):
        clk = FakeClock(1000)
        hlc = HLCClock("aa", clk)
        a = hlc.now()
        clk.t = 2000
        b = hlc.now()
        self.assertEqual(parse_hlc(b)[0], 2000)
        self.assertEqual(parse_hlc(b)[1], 0, "counter resets when physical advances")
        self.assertEqual(compare_hlc(a, b), -1)

    def test_node_id_breaks_ties(self):
        self.assertEqual(compare_hlc("100:0:aa", "100:0:bb"), -1)
        self.assertEqual(compare_hlc("100:0:bb", "100:0:aa"), 1)
        self.assertEqual(compare_hlc("100:0:aa", "100:0:aa"), 0)

    def test_update_preserves_causality(self):
        clk = FakeClock(1000)
        hlc = HLCClock("aa", clk)
        remote = "9999:3:zz"  # far in the "future"
        merged = hlc.update(remote)
        self.assertEqual(compare_hlc(merged, remote), 1)


def _entry(eid, key, ct, created=1, modified=1):
    return {
        "id": eid,
        "key": key,
        "value": {"iv": [0], "ct": [ct]},
        "created": created,
        "modified": modified,
    }


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "kvc.db")
        self.clk = FakeClock()
        self.store = Store(self.db, now_ms=self.clk)

    def tearDown(self):
        self.store.close()

    def test_round_trip_preserves_order_and_values(self):
        entries = [_entry("a", "First", 1), _entry("b", "Second", 2)]
        self.store.replace_all(entries, crypto_key={"kty": "oct", "k": "x"})
        out = self.store.load_all()
        self.assertEqual([e["key"] for e in out["entries"]], ["First", "Second"])
        self.assertEqual(out["entries"][0]["value"]["ct"], [1])
        self.assertEqual(out["cryptoKey"], {"kty": "oct", "k": "x"})

    def test_edit_bumps_rev_only_for_changed_entry(self):
        self.store.replace_all([_entry("a", "K", 1), _entry("b", "K2", 2)])
        # edit only 'a'
        self.clk.t += 5
        self.store.replace_all([_entry("a", "K", 9), _entry("b", "K2", 2)])
        rows = {
            r["id"]: r
            for r in self.store._conn.execute("SELECT id, rev FROM entries").fetchall()
        }
        self.assertEqual(rows["a"]["rev"], 2, "changed entry rev bumped")
        self.assertEqual(rows["b"]["rev"], 1, "unchanged entry rev untouched")

    def test_removed_entry_is_tombstoned_not_returned(self):
        self.store.replace_all([_entry("a", "K", 1), _entry("b", "K2", 2)])
        self.clk.t += 5
        self.store.replace_all([_entry("a", "K", 1)])  # 'b' dropped
        out = self.store.load_all()
        self.assertEqual([e["id"] for e in out["entries"]], ["a"])
        row = self.store._conn.execute(
            "SELECT deleted FROM entries WHERE id='b'"
        ).fetchone()
        self.assertEqual(row["deleted"], 1)

    def test_change_log_grows_only_on_real_changes(self):
        self.store.replace_all([_entry("a", "K", 1)])
        n1 = self.store._conn.execute("SELECT COUNT(*) c FROM change_log").fetchone()["c"]
        # identical PUT -> no new change-log rows
        self.store.replace_all([_entry("a", "K", 1)])
        n2 = self.store._conn.execute("SELECT COUNT(*) c FROM change_log").fetchone()["c"]
        self.assertEqual(n1, n2, "no-op save must not churn the change-log")

    def test_migrate_from_json(self):
        legacy = {
            "entries": [
                {"id": "old1", "key": "Legacy", "value": {"iv": [1], "ct": [2]},
                 "created": 111, "modified": 222},
            ],
            "cryptoKey": {"kty": "oct", "k": "legacy"},
        }
        path = os.path.join(self.tmp, "store.json")
        with open(path, "w") as f:
            json.dump(legacy, f)
        fresh = Store(os.path.join(self.tmp, "fresh.db"), now_ms=self.clk)
        self.assertTrue(fresh.is_empty())
        n = fresh.migrate_from_json(path)
        self.assertEqual(n, 1)
        out = fresh.load_all()
        self.assertEqual(out["entries"][0]["id"], "old1")
        self.assertEqual(out["entries"][0]["key"], "Legacy")
        self.assertEqual(out["entries"][0]["created"], 111)
        self.assertEqual(out["entries"][0]["modified"], 222, "hlc seeded from modified")
        self.assertEqual(out["cryptoKey"], {"kty": "oct", "k": "legacy"})
        fresh.close()

    def test_clock_survives_reopen(self):
        self.store.replace_all([_entry("a", "K", 1)])
        hlc_before = self.store._conn.execute(
            "SELECT hlc FROM change_log ORDER BY seq DESC LIMIT 1"
        ).fetchone()["hlc"]
        self.store.close()
        # reopen with an EARLIER wall clock; new stamps must still be greater
        reopened = Store(self.db, now_ms=FakeClock(1))
        reopened.replace_all([_entry("a", "K", 2)])
        hlc_after = reopened._conn.execute(
            "SELECT hlc FROM change_log ORDER BY seq DESC LIMIT 1"
        ).fetchone()["hlc"]
        self.assertEqual(compare_hlc(hlc_after, hlc_before), 1,
                         "clock must not regress across a restart")
        reopened.close()


if __name__ == "__main__":
    unittest.main()
