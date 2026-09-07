#!/usr/bin/env python3
"""A snowflake is ASCII digits. `\\d` accepts every Unicode decimal digit, so a
19-character Arabic-Indic string satisfied `\\d{17,20}` and travelled as an
authoritative id through the coherence gate and every accessor.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "skills/collaboration-intelligence/scripts"


def _load(name, fn):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / fn)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ri = _load("ri_ascii", "roster_identity.py")
mri = _load("mri_ascii", "migrate_roster_identity.py")

ASCII = "1400000000000000001"
ARABIC = "١٤٠٠٠٠٠٠٠٠٠٠٠٠٠٠٠٠١"


class ASCIIOnlySnowflakes(unittest.TestCase):
    def test_the_fixture_is_in_range_for_the_OLD_pattern(self):
        """Without this the suite could pass on a fixture never in range."""
        import re
        self.assertEqual(len(ARABIC), 19)
        self.assertTrue(re.fullmatch(r"\d{17,20}", ARABIC),
                        "fixture must satisfy the pattern being replaced")

    def test_ascii_still_resolves(self):
        self.assertTrue(ri._is_snowflake_str(ASCII))
        self.assertEqual(mri._SNOWFLAKE.findall(ASCII), [ASCII])

    def test_a_unicode_digit_string_is_not_an_id(self):
        self.assertFalse(ri._is_snowflake_str(ARABIC))

    def test_a_mixed_run_is_rejected_WHOLE_not_trimmed_to_its_ascii_tail(self):
        # Boundaries stay `\\d` on purpose: an ASCII tail adjacent to a Unicode
        # digit must not be extracted as an authoritative id.
        self.assertEqual(mri._SNOWFLAKE.findall("١" + ASCII), [])
        self.assertFalse(ri._is_snowflake_str("١" + ASCII[1:]))

    def test_the_plural_accessor_drops_a_unicode_member(self):
        e = {"_schema": "reviewer-identity/2", "human_discord_id": ASCII,
             "stand_discord_id": "1500000000000000001",
             ri.OTHER_STANDS_FIELD: [{"id": ARABIC}]}
        self.assertFalse(ri.entry_is_coherent(e))
        self.assertEqual(ri.stand_discord_ids(e), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
