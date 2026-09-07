"""One provider id must not be authoritative as BOTH referents in one document.

`entry_is_coherent` validates a single row, and `migrate` emits rows
independently, so roster `alice.stand = H` alongside triage
`people.bob.discord = H` both returned coherent with no unresolved ids — H was
a stand for one person and a human for another in the same v2 map.
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/collaboration-intelligence/scripts/migrate_roster_identity.py"
sys.path.insert(0, str(SCRIPT.parent))
import migrate_roster_identity as m  # noqa: E402

H = "111111111111111111"
T = "222222222222222222"


def rows(*specs):
    out = []
    for login, human, stand in specs:
        out.append({"key": login, "login": login, "after_human": human,
                    "after_stand": stand, "after_other_stands": []})
    return out


class Unit(unittest.TestCase):
    def test_one_id_as_human_HERE_and_stand_THERE_is_a_collision(self):
        c = m.cross_role_collisions(rows(("alice", None, H), ("bob", H, None)))
        self.assertEqual(len(c), 1, c)
        self.assertEqual(c[0]["id"], H)
        self.assertEqual((c[0]["human"], c[0]["stand"]), (["bob"], ["alice"]))

    def test_the_SAME_person_holding_both_referents_is_not_a_collision(self):
        """The entry-local check owns that; flagging it here would refuse every
        ordinary row and make the pass useless."""
        self.assertEqual(m.cross_role_collisions(rows(("alice", H, H))), [])

    def test_two_KEYS_for_one_login_still_COLLIDE_across_roles(self):
        """Alias equivalence may suppress duplicates within a role; it must not
        exempt opposite referents. Two keys canonicalising to one login used to
        take the same-login exemption and publish H as human AND stand."""
        r = rows(("alice", None, H), ("alice", H, None))
        r[1]["key"] = "alice-alt"
        self.assertEqual(len(m.cross_role_collisions(r)), 1)

    def test_an_alias_repeating_ONE_role_is_still_not_a_clash(self):
        """The other half: two keys, one login, same role — a duplicate
        observation, not a collision, and it must stay silent."""
        r = rows(("alice", None, H), ("alice", None, H))
        r[1]["key"] = "alice-alt"
        self.assertEqual(m.cross_role_collisions(r), [])

    def test_DISTINCT_ids_in_distinct_roles_are_clean(self):
        """The control: a pass that flagged everything would satisfy case 1."""
        self.assertEqual(m.cross_role_collisions(rows(("alice", None, H),
                                                      ("bob", T, None))), [])

    def test_an_other_stand_counts_as_a_stand(self):
        r = rows(("alice", None, None), ("bob", H, None))
        r[0]["after_other_stands"] = [H]
        self.assertEqual(len(m.cross_role_collisions(r)), 1)


class Production(unittest.TestCase):
    """Through production `main()`: rc must be nonzero and no file written."""

    def test_main_refuses_and_writes_NOTHING(self):
        d = pathlib.Path(tempfile.mkdtemp())
        roster = d / "roster.json"
        triage = d / "triage.json"
        out = d / "v2.json"
        roster.write_text(json.dumps({"alice": {"stand_status": f"stand id {H}"}}))
        triage.write_text(json.dumps({"people": {"bob": {"discord": H}}}))
        r = subprocess.run([sys.executable, str(SCRIPT), "--roster", str(roster),
                            "--triage-config", str(triage), "--out", str(out)],
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(out.exists(), "a wrong map was published anyway")
        self.assertIn(H, r.stderr)

    def test_a_DISTINCT_pair_still_migrates(self):
        """Control: without this, refusing every document passes the case above."""
        d = pathlib.Path(tempfile.mkdtemp())
        roster = d / "roster.json"
        triage = d / "triage.json"
        out = d / "v2.json"
        roster.write_text(json.dumps({"alice": {"stand_status": f"stand id {H}"}}))
        triage.write_text(json.dumps({"people": {"bob": {"discord": T}}}))
        r = subprocess.run([sys.executable, str(SCRIPT), "--roster", str(roster),
                            "--triage-config", str(triage), "--out", str(out)],
                           capture_output=True, text=True)
        self.assertTrue(out.exists(), r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
