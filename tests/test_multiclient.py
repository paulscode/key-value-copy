#!/usr/bin/env python3
"""Two-client convergence tests — mirrors how two browser tabs actually behave.

Reproduces the reported bug: repeated conflicts on the same entry should keep
accumulating conflict siblings AND both clients should converge to the same
winner. A client only learns state from the ids returned in its sync response
(exactly like app.js applyServerChanges), so a losing push that doesn't echo the
winner leaves that client diverged.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from store import Store  # noqa: E402


class Client:
    """Faithful model of the app.js sync client."""

    def __init__(self, server, node, hlc_base):
        self.s = server
        self.node = node
        self.since = 0
        self.vault = {}          # id -> full entry record (as returned by server)
        self.hc = hlc_base

    def _hlc(self):
        self.hc += 1
        return f"{self.hc}:0:{self.node}"

    def _apply(self, resp):
        self.since = resp["new_since"]
        for e in resp["changes"]:
            self.vault[e["id"]] = e   # upsert exactly like the client does

    def pull(self):
        self._apply(self.s.apply_sync(self.since, []))

    def make_edit(self, eid, value, key="K"):
        cur = self.vault.get(eid)
        base = cur["rev"] if cur else 0
        return {"id": eid, "key": key, "value": value,
                "base_rev": base, "hlc": self._hlc(), "deleted": False}

    def push(self, changes):
        self._apply(self.s.apply_sync(self.since, changes, device_hlc=self._hlc()))

    def live(self):
        return {e["id"]: e for e in self.vault.values() if not e.get("deleted")}


class TwoClientConvergence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.server = Store(os.path.join(self.tmp, "kvc.db"))

    def tearDown(self):
        self.server.close()

    def test_repeated_conflicts_accumulate_and_converge(self):
        # A always has the higher HLC, so A wins every race and B always loses
        # (the "current wins" branch — the path that failed to echo the winner).
        A = Client(self.server, "A", hlc_base=5000)
        B = Client(self.server, "B", hlc_base=1000)

        A.push([{"id": "a", "key": "K", "value": {"ct": 0},
                 "base_rev": 0, "hlc": A._hlc(), "deleted": False}])
        B.pull()  # B learns entry a

        for k in range(1, 4):  # three rounds of conflict
            eA = A.make_edit("a", {"ct": 100 + k})
            eB = B.make_edit("a", {"ct": 200 + k})
            A.push([eA])   # fast-forwards; a := A's value
            B.push([eB])   # conflict; A wins, B's value should become a sibling
            A.pull()
            B.pull()

        server_siblings = [
            e for e in self.server.changes_since(0)["changes"]
            if not e["deleted"] and e["conflict"] is not None
        ]
        self.assertEqual(len(server_siblings), 3,
                         "each of the 3 conflicts must preserve a sibling")

        # both clients converge to identical live state
        a_of = lambda c: c.live()["a"]["value"]
        self.assertEqual(a_of(A), a_of(B), "clients disagree on the winner")
        self.assertEqual(sorted(e["key"] for e in A.live().values()),
                         sorted(e["key"] for e in B.live().values()),
                         "clients disagree on which entries exist")
        self.assertEqual(len(A.live()), 4, "a + 3 conflict siblings")


if __name__ == "__main__":
    unittest.main()
