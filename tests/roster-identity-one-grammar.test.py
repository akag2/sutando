r"""The snowflake grammar has ONE owner, and each consumer's delegation is pinned.

Three independent copies existed: `_is_snowflake_str`, an inline literal inside
`_snowflake_list`, and the migrator's `_SNOWFLAKE`. The mutation that exposed it:
putting `\d{17,20}` back into `_snowflake_list` alone left every suite green, so
the ASCII fix was unpinned at that call site. Counting regex literals would not
catch a fourth consumer; a delegation check does.
"""
import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]
                       / "skills/collaboration-intelligence/scripts"))
import roster_identity as ri            # noqa: E402
import migrate_roster_identity as mg    # noqa: E402

ASCII = "1" * 18
CONFUSABLE = "٣" * 18          # Arabic-Indic: `\d` matches these, `[0-9]` does not
FULLWIDTH = "１" * 18


class OneOwner(unittest.TestCase):
    def test_the_grammar_is_a_single_named_constant(self):
        self.assertEqual(ri.SNOWFLAKE_CORE, r"[0-9]{17,20}")

    def test_the_EXTRACTOR_is_built_from_it(self):
        """Boundaries are the extractor's own; the core must not be re-typed."""
        self.assertIn(ri.SNOWFLAKE_CORE, mg._SNOWFLAKE.pattern)

    def test_the_LIST_consumer_DELEGATES_to_the_validator(self):
        """The discriminator: replace the validator and the consumer must follow.
        A private copy passes every value test while ignoring this."""
        orig = ri._is_snowflake_str
        try:
            ri._is_snowflake_str = lambda v: False
            self.assertEqual(ri._snowflake_list([ASCII]), [],
                             "_snowflake_list keeps its own grammar")
        finally:
            ri._is_snowflake_str = orig
        self.assertEqual(ri._snowflake_list([ASCII]), [ASCII])


class EveryPathAgrees(unittest.TestCase):
    """Scalar, list and serialized-container surfaces, ASCII-valid vs confusable."""

    def test_scalar(self):
        self.assertTrue(ri._is_snowflake_str(ASCII))
        self.assertFalse(ri._is_snowflake_str(CONFUSABLE))
        self.assertFalse(ri._is_snowflake_str(FULLWIDTH))

    def test_list(self):
        self.assertEqual(ri._snowflake_list([ASCII, CONFUSABLE, FULLWIDTH]), [ASCII])

    def test_extractor_in_running_text(self):
        self.assertEqual(mg._snowflakes(f"id {ASCII} here"), [ASCII])
        self.assertEqual(mg._snowflakes(f"id {CONFUSABLE} here"), [])

    def test_a_MIXED_run_is_rejected_whole(self):
        """The boundaries are why: an ASCII tail inside a confusable run must not
        be mined as an id."""
        self.assertEqual(mg._snowflakes(CONFUSABLE + ASCII), [])

    def test_a_JSON_NUMBER_is_not_a_snowflake_on_any_path(self):
        n = int(ASCII)
        self.assertFalse(ri._is_snowflake_str(n))
        self.assertEqual(ri._snowflake_list([n]), [])


if __name__ == "__main__":
    unittest.main()
