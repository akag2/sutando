"""Carried-refusal revalidation must ask the COLLECTOR, not a second parser.

`_still_unresolved` used `_mineable_now`, which scanned raw JSON for digits with
none of the collector's path, provider or identity-leaf rules, and walked the
finding's path through dicts only. Two consequences, in opposite directions:
an irrelevant snowflake in unreadable metadata cleared a real refusal, and a
repair on a documented `identities[]` path could never clear one.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]
                       / "skills/collaboration-intelligence/scripts"))
import migrate_roster_identity as m  # noqa: E402

H = "1400000000000000001"
T = "1600000000000000001"


def rec(path):
    return {"path": path, "kind": "unreadable", "reason": "x"}


class CarriedRevalidation(unittest.TestCase):
    def test_an_irrelevant_snowflake_in_metadata_cannot_clear(self):
        """`_collect_ids({'stand_status': {'note': T}})` is [], so T is not an
        id the migration would ever read; it must not retire the finding."""
        self.assertEqual(m._collect_ids({"stand_status": {"note": T}}), [])
        self.assertTrue(m._still_unresolved({"stand_status": {"note": T}},
                                            rec("stand_status"), set()))

    def test_a_FLAT_dotted_key_clears(self):
        """The reviewer's discriminator. `discord.human.user_id` is ONE key, not
        three levels; a path re-parsed with `split(".")` looked for nested dicts
        and found nothing, so a real repair could never clear. The collector
        reads the flat key, so revalidation must ask the collector."""
        e = {"discord.human.user_id": H}
        self.assertEqual(m._collect_ids(e), [H])
        self.assertFalse(m._still_unresolved(e, rec("discord.human.user_id"), set()))

    def test_revalidation_consumes_the_collectors_own_path_map(self):
        """Delegation, not agreement: every path the check can answer about is a
        path `mined_paths` reports, so a fourth spelling cannot diverge."""
        e = {"human": {"provider": "discord", "identities": [{"user_id": H}]}}
        self.assertIn("human.identities.user_id", m.mined_paths(e))
        self.assertEqual(m.mined_paths(e)["human.identities.user_id"], [H])
        self.assertFalse(hasattr(m, "_nodes_at"))

    def test_a_repair_on_a_LIST_path_clears(self):
        """A list does not consume a path segment — the rule the collector
        applies. A dict-only descent made this path permanently unreachable,
        so the repair could never retire the refusal."""
        e = {"human": {"provider": "discord", "identities": [{"user_id": H}]}}
        self.assertEqual(m._collect_ids(e), [H])
        self.assertFalse(m._still_unresolved(e, rec("human.identities.user_id"), set()))

    def test_the_PROVIDER_is_carried_down_the_path(self):
        """Detached from the dict that declares it, an identity leaf stops being
        Discord and every repair reads as still-broken. Same value, provider
        removed, is not readable and must stay refused — that pair is the test."""
        with_p = {"human": {"provider": "discord", "account": {"user_id": H}}}
        without = {"human": {"account": {"user_id": H}}}
        self.assertEqual(m._collect_ids(with_p), [H])
        self.assertEqual(m._collect_ids(without), [])
        self.assertFalse(m._still_unresolved(with_p, rec("human.account.user_id"), set()))
        self.assertTrue(m._still_unresolved(without, rec("human.account.user_id"), set()))

    def test_a_NON_id_at_a_readable_path_stays_refused(self):
        """Control: the fix must not clear a path merely because it is now
        reachable. Reachable and readable are different questions."""
        e = {"human": {"provider": "discord", "identities": [{"user_id": "nope"}]}}
        self.assertTrue(m._still_unresolved(e, rec("human.identities.user_id"), set()))

    def test_blank_and_absent_still_refuse(self):
        """Controls on the two pre-existing branches, so the rewrite cannot
        quietly drop them."""
        blank = {"human": {"provider": "discord", "account": {"user_id": "  "}}}
        self.assertTrue(m._still_unresolved(blank, rec("human.account.user_id"), set()))
        self.assertTrue(m._still_unresolved({}, rec("no.such.path"), set()))

    def test_the_second_parser_is_GONE(self):
        """Delegation is the point; a surviving copy would drift again."""
        self.assertFalse(hasattr(m, "_mineable_now"))


if __name__ == "__main__":
    unittest.main()
