r"""Serialization syntax must never become identity evidence.

`_bad()` fed `json.dumps()` into the extractor. `json.dumps` escapes a non-ASCII
digit to `\uXXXX`, and those hex digits join whatever follows: `"１" + "1"*15`
serialises to `"１111111111111111"`, from which a 17-digit run extracts as
an id present nowhere in the source. Reviewer's exact-head discriminator.
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

FABRICATING = "１" + "1" * 15          # fullwidth ONE, then ASCII
REAL = "1" * 18
SID = "1504316176686120980"


class EscapesAreNotEvidence(unittest.TestCase):
    def test_the_source_holds_no_ascii_snowflake(self):
        """Precondition: without this the rest proves nothing."""
        self.assertEqual(m._snowflakes(FABRICATING), [])

    def test_SERIALISING_it_fabricates_one(self):
        """The defect, pinned so a return to json.dumps is visible."""
        self.assertEqual(m._snowflakes(json.dumps(FABRICATING)),
                         ["1" * 17])

    def test_the_scalar_walker_does_not(self):
        self.assertEqual(m._scalar_snowflakes({"note": FABRICATING}), [])

    def test_a_real_id_nested_in_containers_is_still_found(self):
        """Control: refusing everything would pass the case above for free."""
        self.assertEqual(m._scalar_snowflakes({"a": [{"b": REAL}]}), [REAL])

    def test_a_NUMERIC_id_is_still_evidence(self):
        """A JSON number is never an authoritative id, but it still OPPOSES a
        slot — dropping it silently published the opposite referent."""
        self.assertEqual(m._scalar_snowflakes({"discord": int(SID)}), [SID])

    def test_a_BOOL_is_not_a_number_here(self):
        self.assertEqual(m._scalar_snowflakes({"flag": True}), [])


# NO PRODUCTION ARM, deliberately: restoring `json.dumps` at the call site gave
# byte-identical output on every fixture, so one would pass in both states.


if __name__ == "__main__":
    unittest.main()
