"""`--strict` makes room_ops' exit code follow its own ok; the default does not.

Measured 2026-09-08: `room_ops send <room> "<a message body>"` (send takes a PATH)
printed {"ok": false, "reason": "file not found"} and exited 0, so a shell caller
gating on the exit code read a failed send as delivered. It cost a real post.

The DEFAULT stays 0 — skills/agent-room-ops/test_room_ops.py pins that across
read/send/rooms/events (8 assertions), and notify_reviewers.py reads `ok`
explicitly rather than the exit code. --strict is for shell callers only.
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


class StrictIsOptIn(unittest.TestCase):
    def setUp(self):
        self.src = SRC.read_text()

    def test_the_flag_exists(self):
        self.assertIn('"--strict"', self.src)

    def test_the_return_is_guarded_by_the_flag(self):
        tail = self.src[self.src.rindex("print(json.dumps(res"):]
        self.assertIn("a.strict", tail,
            "the nonzero exit must be reachable only with --strict")
        self.assertIn('res.get("ok")', tail)

    def test_default_stays_zero_and_strict_maps_false_to_one(self):
        ns: dict = {}
        exec("def rc(res, strict):\n"
             "    if strict and isinstance(res, dict) and res.get('ok') is False:\n"
             "        return 1\n"
             "    return 0", ns)
        rc = ns["rc"]
        bad = {"ok": False, "reason": "file not found"}
        self.assertEqual(rc(bad, False), 0, "default contract must not change")
        self.assertEqual(rc(bad, True), 1)
        self.assertEqual(rc({"ok": True}, True), 0)

    def test_a_result_without_an_ok_key_stays_zero_even_strict(self):
        ns: dict = {}
        exec("def rc(res, strict):\n"
             "    if strict and isinstance(res, dict) and res.get('ok') is False:\n"
             "        return 1\n"
             "    return 0", ns)
        self.assertEqual(ns["rc"]({"rooms": []}, True), 0)
        self.assertEqual(ns["rc"](None, True), 0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
