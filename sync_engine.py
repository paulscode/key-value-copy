#!/usr/bin/env python3
"""Sync primitives for Key Value Copy — Hybrid Logical Clock (HLC).

Pure, dependency-free, and unit-testable. This module is the shared conceptual
spec for the Android client too: both sides must build and compare HLCs the same
way for offline edits to converge to the same winner without coordination.

An HLC is serialised as "<physical_ms>:<counter>:<node_id>", e.g.
"1710000000123:4:2a". It is monotonic even when a device's wall clock jumps
backwards, and it defines a deterministic total order via (physical, counter,
node_id).

Conflict *resolution* (apply-a-change-against-current-state) lands with the sync
protocol; this module currently provides the clock + ordering it will build on.
"""

import threading


def parse_hlc(s):
    """"<physical>:<counter>:<node>" -> (physical:int, counter:int, node:str)."""
    physical, counter, node = s.split(":", 2)
    return int(physical), int(counter), node


def format_hlc(physical, counter, node):
    return f"{physical}:{counter}:{node}"


def compare_hlc(a, b):
    """Total order over HLC strings. Returns -1, 0, or 1.

    Compares by (physical, counter, node_id) — node_id breaks ties so two nodes
    that stamped the same (physical, counter) still order deterministically.
    """
    pa, ca, na = parse_hlc(a)
    pb, cb, nb = parse_hlc(b)
    if pa != pb:
        return -1 if pa < pb else 1
    if ca != cb:
        return -1 if ca < cb else 1
    if na != nb:
        return -1 if na < nb else 1
    return 0


DEFAULT_POLICY = {
    "conflict_strategy": "preserve",  # 'preserve' | 'lww'
    "delete_strategy": "edit-wins",   # 'edit-wins' | 'delete-wins'
}


def _content(x):
    """Canonical, conflict-free content extracted from an entry or a change.

    Carries the authoring `hlc` so the store persists the *author's* logical
    timestamp (the basis for the conflict tiebreak), not a server re-stamp.
    """
    return {
        "key": x.get("key", ""),
        "value": x.get("value"),
        "order": x.get("order"),
        "hlc": x.get("hlc"),
        "deleted": bool(x.get("deleted")),
        "conflict": None,
    }


def _flagged(entry, reason):
    c = _content(entry)
    c["conflict"] = {"reason": reason}
    return c


def resolve(current, change, policy=None):
    """Decide how one incoming change reconciles against the current stored entry.

    Pure and deterministic — the Android client runs the identical logic so both
    sides converge on the same result without coordination. Inputs are plain
    dicts; `current` is None if the server has never seen this id.

    Returns a plan:
        {
          "canonical": None | <content>,   # what entry[id] becomes; None = leave as-is
          "sibling":   None | <content>,   # losing edit to preserve as a NEW flagged entry
          "result":    "ok" | "conflict",
        }
    Duplicate-key handling (two *different* ids sharing a key) is not here — it's
    a storage concern applied when a brand-new entry lands (see store).
    """
    policy = policy or DEFAULT_POLICY
    inc_deleted = bool(change.get("deleted"))

    # Brand-new id (or one the server hard-deleted): accept as-is.
    if current is None:
        return {"canonical": _content(change), "sibling": None, "result": "ok"}

    cur_deleted = bool(current.get("deleted"))

    # Fast-forward: the change was based on exactly what we have — no divergence.
    if change.get("base_rev", 0) == current["rev"]:
        return {"canonical": _content(change), "sibling": None, "result": "ok"}

    # ── Divergence (base_rev != current rev): a genuine conflict ──
    inc_newer = compare_hlc(change.get("hlc", "0:0:"), current.get("hlc", "0:0:")) > 0

    # update vs delete ------------------------------------------------
    if inc_deleted and not cur_deleted:
        if policy["delete_strategy"] == "delete-wins":
            return {"canonical": _content(change), "sibling": None, "result": "conflict"}
        # edit-wins: the live edit survives, flagged; the delete is dropped.
        return {"canonical": _flagged(current, "delete-vs-edit"), "sibling": None,
                "result": "conflict"}
    if cur_deleted and not inc_deleted:
        if policy["delete_strategy"] == "delete-wins":
            return {"canonical": None, "sibling": None, "result": "conflict"}  # keep tombstone
        # edit-wins: resurrect with the incoming edit, flagged for review.
        c = _content(change)
        c["conflict"] = {"reason": "resurrect"}
        return {"canonical": c, "sibling": None, "result": "conflict"}
    if inc_deleted and cur_deleted:
        # both tombstoned — keep the later one, nothing to preserve.
        return {"canonical": _content(change) if inc_newer else None,
                "sibling": None, "result": "ok"}

    # update vs update (both live edits) ------------------------------
    if current["key"] == change.get("key") and current.get("value") == change.get("value"):
        return {"canonical": None, "sibling": None, "result": "ok"}  # already converged

    if policy["conflict_strategy"] == "lww":
        return {"canonical": _content(change) if inc_newer else None,
                "sibling": None, "result": "conflict"}

    # preserve: winner is canonical, loser is kept as a flagged sibling.
    if inc_newer:
        return {"canonical": _content(change), "sibling": _content(current), "result": "conflict"}
    return {"canonical": None, "sibling": _content(change), "result": "conflict"}


class HLCClock:
    """A thread-safe Hybrid Logical Clock for one node.

    `now_ms` is an injectable callable returning the current wall-clock time in
    milliseconds (injectable so tests can drive time deterministically).
    """

    def __init__(self, node_id, now_ms):
        self.node = node_id
        self._now = now_ms
        self._last_p = 0
        self._last_c = 0
        self._lock = threading.Lock()

    def now(self):
        """Stamp and return a new HLC for a local event."""
        with self._lock:
            pt = int(self._now())
            if pt > self._last_p:
                self._last_p, self._last_c = pt, 0
            else:
                # wall clock didn't advance (or went backwards): keep the last
                # physical time and bump the logical counter to stay monotonic.
                self._last_c += 1
            return format_hlc(self._last_p, self._last_c, self.node)

    def update(self, remote_hlc):
        """Merge an observed remote HLC (causal receive) and stamp a new local one.

        Guarantees the returned HLC is strictly greater than both our previous
        HLC and the remote one, so causality is preserved across a sync.
        """
        with self._lock:
            rp, rc, _ = parse_hlc(remote_hlc)
            pt = int(self._now())
            new_p = max(pt, self._last_p, rp)
            if new_p == self._last_p and new_p == rp:
                new_c = max(self._last_c, rc) + 1
            elif new_p == self._last_p:
                new_c = self._last_c + 1
            elif new_p == rp:
                new_c = rc + 1
            else:
                new_c = 0
            self._last_p, self._last_c = new_p, new_c
            return format_hlc(new_p, new_c, self.node)
