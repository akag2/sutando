"""An all-null local row must not shadow a usable peer row in roster_union.

Built on the host where this reproduces. Arm 1 FAILS at the parent commit; arms 2-4 pass there and must keep passing,
because the fix is a CONDITIONAL tie-break, not a reordering.

The fixture is measured, not invented: the two rows below are the shapes actually
on disk here for `qingyun-wu` (local placeholder, peer complete).
"""
import importlib.util
import json
import pathlib
import tempfile
import unittest

SRC = (pathlib.Path(__file__).resolve().parents[1] /
       "skills" / "collaboration-intelligence" / "scripts" / "roster_union.py")

def _load(p):
    s = importlib.util.spec_from_file_location("ru", p)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m

LOCAL_PLACEHOLDER = {"stand": None, "room": None, "discord": None}
PEER_COMPLETE     = {"stand": "@sutando-qingyun-001:ag2.space", "room": "!r:ag2.space"}


class ShadowedByAnEmptyLocalRow(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.local = pathlib.Path(self.d, "local.json")
        self.peer  = pathlib.Path(self.d, "peer.json")
        self.local.write_text(json.dumps({"qingyun-wu": LOCAL_PLACEHOLDER}))
        self.peer.write_text(json.dumps({"qingyun-wu": PEER_COMPLETE}))
        self.m = _load(SRC)

    def test_a_usable_peer_row_is_not_shadowed_by_an_empty_local_one(self):
        u = self.m.roster_union([("local", self.local), ("peer", self.peer)])
        row = u["qingyun-wu"]
        self.assertTrue(row.get("stand") and row.get("room"),
            "the bare key resolves to the placeholder; the usable row is only "
            f"reachable as 'qingyun-wu@peer'. union keys: {sorted(u)}")

    def test_the_peer_row_is_still_retained_under_its_suffix(self):
        """The fix must not drop the losing row — that property is deliberate."""
        u = self.m.roster_union([("local", self.local), ("peer", self.peer)])
        self.assertTrue(any(k.startswith("qingyun-wu@") for k in u), sorted(u))

    def test_origin_order_still_decides_when_BOTH_rows_are_usable(self):
        """Completeness breaks the tie; it does not replace local-wins."""
        self.local.write_text(json.dumps({"k": {"stand": "@a:x", "room": "!a:x"}}))
        self.peer.write_text(json.dumps({"k": {"stand": "@b:x", "room": "!b:x"}}))
        u = self.m.roster_union([("local", self.local), ("peer", self.peer)])
        self.assertEqual(u["k"]["stand"], "@a:x")

    def test_origin_order_still_decides_when_BOTH_rows_are_empty(self):
        self.local.write_text(json.dumps({"k": {"stand": None, "note": "local"}}))
        self.peer.write_text(json.dumps({"k": {"stand": None, "note": "peer"}}))
        u = self.m.roster_union([("local", self.local), ("peer", self.peer)])
        self.assertEqual(u["k"]["note"], "local")

    def test_a_deliberate_local_REFUSAL_is_not_treated_as_a_placeholder(self):
        """Blank stand/room PLUS refusal_basis is DO-NOT-ROUTE per schema.md, not
        missing data. keweichen, reviewing this PR: the tie-break must not route
        around it, or a synced peer row silently overrides an explicit refusal."""
        self.local.write_text(json.dumps({"qingyun-wu": {
            "stand": "", "room": "", "refusal_basis": "DO NOT ROUTE — asked off-channel"}}))
        self.peer.write_text(json.dumps({"qingyun-wu": PEER_COMPLETE}))
        u = self.m.roster_union([("local", self.local), ("peer", self.peer)])
        self.assertEqual(u["qingyun-wu"].get("refusal_basis"),
                         "DO NOT ROUTE — asked off-channel",
                         f"the refusal lost the collision; union: {sorted(u)}")

    def test_a_note_alone_also_protects_the_row(self):
        self.local.write_text(json.dumps({"k": {"stand": None, "note": "human-only by request"}}))
        self.peer.write_text(json.dumps({"k": {"stand": "@b:x", "room": "!b:x"}}))
        u = self.m.roster_union([("local", self.local), ("peer", self.peer)])
        self.assertEqual(u["k"].get("note"), "human-only by request")


if __name__ == "__main__":
    unittest.main(verbosity=2)
