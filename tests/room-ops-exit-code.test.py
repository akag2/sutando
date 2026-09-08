"""room_ops must exit nonzero when its own result says ok:false.

Measured 2026-09-08 on a live host: `room_ops send <room> "<a message body>"`
(send takes a PATH, not a body) printed {"ok": false, "reason": "file not
found"} and exited 0. A caller gating on the exit code — every `&&` chain and
every `set -e` script — reads that as a delivered message. `say` to an
unreachable room does the same with reason "HTTP 502".

notify_reviewers.py guards by reading `ok` explicitly, so it is unaffected;
the exposure is shell callers, which is the interface the skill documents.
"""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

SRC = (pathlib.Path(__file__).resolve().parents[1] /
       "skills" / "agent-room-ops" / "room_ops.py")


def _load():
    s = importlib.util.spec_from_file_location("ro", SRC)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


class ExitCodeFollowsOk(unittest.TestCase):
    """The exit code is derived from res['ok'], not hardcoded."""

    def setUp(self):
        self.src = SRC.read_text()

    def test_main_does_not_hardcode_a_zero_return(self):
        tail = self.src[self.src.rindex("print(json.dumps(res"):]
        self.assertNotIn("\n    return 0\n", tail,
            "_main returns 0 unconditionally; a printed ok:false still exits 0")

    def test_the_return_consults_ok(self):
        tail = self.src[self.src.rindex("print(json.dumps(res"):]
        self.assertIn('res.get("ok")', tail,
            "the exit code must be derived from the result's own verdict")

    def test_a_false_ok_maps_to_nonzero_and_true_to_zero(self):
        ns: dict = {}
        exec("def rc(res):\n"
             "    return 1 if isinstance(res, dict) and res.get('ok') is False else 0",
             ns)
        rc = ns["rc"]
        self.assertEqual(rc({"ok": False, "reason": "file not found"}), 1)
        self.assertEqual(rc({"ok": False, "reason": "HTTP 502"}), 1)
        self.assertEqual(rc({"ok": True, "event_id": "$x"}), 0)

    def test_a_result_without_an_ok_key_still_exits_zero(self):
        """Only an explicit False fails: commands whose result carries no `ok`
        must keep their previous exit code, or this fix breaks them."""
        ns: dict = {}
        exec("def rc(res):\n"
             "    return 1 if isinstance(res, dict) and res.get('ok') is False else 0",
             ns)
        self.assertEqual(ns["rc"]({"rooms": []}), 0)
        self.assertEqual(ns["rc"](None), 0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
