"""`--out` must not overwrite ANY supplied input, by name or by hardlink.

The shipped guard checked only `--roster`, so `--triage-config X --out X`
returned 0, replaced X's `people` map with the v2 roster, and printed
"input untouched" — the message a caller would have trusted.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPT = (pathlib.Path(__file__).resolve().parents[1]
          / "skills/collaboration-intelligence/scripts/migrate_roster_identity.py")

BOT = "111111111111111111"
# A shape the migration can actually RESOLVE: an unresolvable one exits 5 and
# the positive control then fails for a reason that has nothing to do with aliasing.
ROSTER = {"rui": {"github": "john-the-dev", "stand_status": f"stand id {BOT}"}}
TRIAGE = {"people": {"bob": {"discord": "333333333333333333"}}}


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                          capture_output=True, text=True)


class OutAlias(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp())
        self.roster = self.d / "roster.json"
        self.triage = self.d / "triage.json"
        self.roster.write_text(json.dumps(ROSTER))
        self.triage.write_text(json.dumps(TRIAGE))

    def test_out_equal_to_the_roster_is_refused(self):
        r = run("--roster", self.roster, "--out", self.roster)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(json.loads(self.roster.read_text()), ROSTER)

    def test_out_equal_to_the_TRIAGE_config_is_refused(self):
        before = self.triage.read_text()
        r = run("--roster", self.roster, "--triage-config", self.triage,
                "--out", self.triage)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.triage.read_text(), before,
                         "the auxiliary input was rewritten")

    def test_a_HARDLINK_to_an_input_is_refused(self):
        """A different name, the same inode: resolve() alone cannot see it."""
        link = self.d / "alias.json"
        os.link(self.triage, link)
        before = self.triage.read_text()
        r = run("--roster", self.roster, "--triage-config", self.triage, "--out", link)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.triage.read_text(), before)

    def test_a_NORMAL_sibling_output_still_succeeds(self):
        """The control: a guard that refuses everything is not a guard."""
        out = self.d / "roster.v2.json"
        r = run("--roster", self.roster, "--triage-config", self.triage, "--out", out)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(out.exists())
        self.assertEqual(json.loads(self.triage.read_text()), TRIAGE)


if __name__ == "__main__":
    unittest.main()
