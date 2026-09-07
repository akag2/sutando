"""core_state_notice — the "why am I silent" sweep.

Covers the notice/recovery lifecycle a room observes across a core outage:
degraded → one notice per (room, reason) per cooldown, failed sends retried
without burning the notice, recovery announced exactly once, and every
no-verdict input (absent/stale/malformed file, unrecognized state, kill
switch) doing NOTHING — the module's failure mode must be silence, not spam.
"""
import json
import os
import pathlib
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ag2_sparrow import core_state_notice as csn


class _Sender:
    def __init__(self, ok=True):
        self.ok = ok
        self.sent = []  # (room, body)

    def __call__(self, room, body):
        if self.ok:
            self.sent.append((room, body))
        return self.ok


def _write_state(tmp, state, kind=None, mtime=None):
    p = tmp / csn.CORE_SUPERVISOR_FILE
    p.write_text(json.dumps({"state": state, "kind": kind,
                             "detail": "x", "prompt": None}))
    if mtime is not None:
        os.utime(p, (mtime, mtime))


def test_no_supervisor_file_does_nothing():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        s = _Sender()
        csn.sweep_core_state_notices(tmp, {"!r:s"}, s)
        assert s.sent == []
        assert not (tmp / csn.LEDGER_FILE).exists()


def test_degraded_notices_once_per_room_with_cooldown():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        _write_state(tmp, "blocked-human", kind="session-limit")
        s = _Sender()
        now = time.time()
        csn.sweep_core_state_notices(tmp, {"!a:s", "!b:s"}, s, now=now)
        assert sorted(r for r, _ in s.sent) == ["!a:s", "!b:s"]
        assert all("usage limit" in b for _, b in s.sent)
        # same reason inside the cooldown → silent (per room)
        csn.sweep_core_state_notices(tmp, {"!a:s", "!b:s"}, s, now=now + 60)
        assert len(s.sent) == 2
        # a NEW room mid-outage still gets its notice
        csn.sweep_core_state_notices(tmp, {"!a:s", "!c:s"}, s, now=now + 61)
        assert [r for r, _ in s.sent].count("!c:s") == 1
        # past the cooldown the same room re-notices (long outage: one
        # reminder per window, not one per message). The watcher rewrites the
        # file every tick in production; mirror that or it goes stale first.
        later = now + csn._cooldown_s() + 1
        _write_state(tmp, "blocked-human", kind="session-limit", mtime=later)
        csn.sweep_core_state_notices(tmp, {"!a:s"}, s, now=later)
        assert [r for r, _ in s.sent].count("!a:s") == 2


def test_failed_send_burns_nothing_and_retries():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        _write_state(tmp, "logged-out")
        now = time.time()
        failing = _Sender(ok=False)
        csn.sweep_core_state_notices(tmp, {"!a:s"}, failing, now=now)
        assert failing.sent == []
        ok = _Sender()
        csn.sweep_core_state_notices(tmp, {"!a:s"}, ok, now=now + 1)
        assert len(ok.sent) == 1 and "logged out" in ok.sent[0][1]


def test_reason_change_renotices_inside_cooldown():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        _write_state(tmp, "blocked-human", kind="session-limit")
        s = _Sender()
        now = time.time()
        csn.sweep_core_state_notices(tmp, {"!a:s"}, s, now=now)
        _write_state(tmp, "crashed")
        csn.sweep_core_state_notices(tmp, {"!a:s"}, s, now=now + 5)
        assert len(s.sent) == 2
        assert "usage limit" in s.sent[0][1] and "not running" in s.sent[1][1]


def test_recovery_announced_once_and_flap_stays_bounded():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        _write_state(tmp, "blocked-human", kind="session-limit")
        s = _Sender()
        now = time.time()
        csn.sweep_core_state_notices(tmp, {"!a:s"}, s, now=now)
        _write_state(tmp, "idle-ready")
        # recovery goes to the noticed room even if its task already resolved
        # (rooms arg empty) — the sender was told "I'm down", so tell them back
        csn.sweep_core_state_notices(tmp, set(), s, now=now + 5)
        assert len(s.sent) == 2 and "back online" in s.sent[1][1]
        # healthy again → nothing more
        csn.sweep_core_state_notices(tmp, set(), s, now=now + 6)
        assert len(s.sent) == 2
        # degraded/healthy FLAP (review should-fix #1): recovery must NOT
        # clear the cooldown — the same reason inside the window stays silent,
        # and with no notice delivered, no second recovery is owed either
        _write_state(tmp, "blocked-human", kind="session-limit")
        csn.sweep_core_state_notices(tmp, {"!a:s"}, s, now=now + 7)
        _write_state(tmp, "idle-ready")
        csn.sweep_core_state_notices(tmp, set(), s, now=now + 8)
        assert len(s.sent) == 2
        # past the cooldown a genuine new outage notices again
        later = now + csn._cooldown_s() + 1
        _write_state(tmp, "blocked-human", kind="session-limit", mtime=later)
        csn.sweep_core_state_notices(tmp, {"!a:s"}, s, now=later)
        assert len(s.sent) == 3


def test_alternating_reasons_bounded_per_reason():
    # Review should-fix #1: usage-limit ↔ logged-out must not re-send on every
    # alternation — each reason keeps ITS OWN cooldown for the room.
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        s = _Sender()
        now = time.time()
        seq = [("blocked-human", "session-limit"), ("logged-out", None),
               ("blocked-human", "session-limit"), ("logged-out", None),
               ("blocked-human", "session-limit")]
        for i, (state, kind) in enumerate(seq):
            _write_state(tmp, state, kind=kind)
            csn.sweep_core_state_notices(tmp, {"!a:s"}, s, now=now + i)
        assert len(s.sent) == 2  # one per reason, not one per alternation
        assert "usage limit" in s.sent[0][1] and "logged out" in s.sent[1][1]


def test_v1_ledger_resets_cleanly():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        (tmp / csn.LEDGER_FILE).write_text(json.dumps({
            "schema_version": 1,
            "noticed": {"!a:s": {"reason": "usage-limit", "ts": time.time()}}}))
        _write_state(tmp, "blocked-human", kind="session-limit")
        s = _Sender()
        csn.sweep_core_state_notices(tmp, {"!a:s"}, s)
        # v1 state is unreadable under v2 → reset: one (duplicate) notice
        # beats wedging, and the file is rewritten as v2
        assert len(s.sent) == 1
        assert json.loads((tmp / csn.LEDGER_FILE).read_text())["schema_version"] == 2


def test_no_verdict_inputs_do_nothing():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        s = _Sender()
        now = time.time()
        # stale file: the watcher is dead — its verdict is history, not state
        _write_state(tmp, "crashed", mtime=now - csn.STALE_STATE_S - 1)
        csn.sweep_core_state_notices(tmp, {"!a:s"}, s, now=now)
        # unrecognized state: neither a notice nor proof of recovery
        _write_state(tmp, "gateway-down")
        csn.sweep_core_state_notices(tmp, {"!a:s"}, s, now=now)
        # malformed / non-dict
        (tmp / csn.CORE_SUPERVISOR_FILE).write_text("[]")
        csn.sweep_core_state_notices(tmp, {"!a:s"}, s, now=now)
        (tmp / csn.CORE_SUPERVISOR_FILE).write_text("{nope")
        csn.sweep_core_state_notices(tmp, {"!a:s"}, s, now=now)
        assert s.sent == []


def test_unrecognized_state_does_not_fake_recovery():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        s = _Sender()
        now = time.time()
        _write_state(tmp, "crashed")
        csn.sweep_core_state_notices(tmp, {"!a:s"}, s, now=now)
        _write_state(tmp, "gateway-down")
        csn.sweep_core_state_notices(tmp, set(), s, now=now + 5)
        assert len(s.sent) == 1  # no recovery line
        _write_state(tmp, "running")
        csn.sweep_core_state_notices(tmp, set(), s, now=now + 6)
        assert len(s.sent) == 2 and "back online" in s.sent[1][1]


def test_kill_switch_env_disables_everything():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        _write_state(tmp, "crashed")
        s = _Sender()
        os.environ["SPARROW_CORE_NOTICE"] = "0"
        try:
            csn.sweep_core_state_notices(tmp, {"!a:s"}, s)
        finally:
            del os.environ["SPARROW_CORE_NOTICE"]
        assert s.sent == []


def test_bodies_are_fixed_strings_never_file_content():
    # Rooms span tiers; the supervisor file carries core pane text. Nothing
    # from the file may reach a room body — only this module's fixed phrases.
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        marker = "SECRET-PANE-CONTENT"
        (tmp / csn.CORE_SUPERVISOR_FILE).write_text(json.dumps({
            "state": "blocked-human", "kind": "session-limit",
            "detail": marker, "prompt": marker}))
        s = _Sender()
        csn.sweep_core_state_notices(tmp, {"!a:s"}, s)
        assert len(s.sent) == 1 and marker not in s.sent[0][1]


def test_read_core_state_bounds_and_shapes():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        _write_state(tmp, "x" * 1000, kind=123)
        state, kind = csn.read_core_state(tmp)
        assert len(state) == csn._FIELD_MAX and kind is None
        assert csn.degraded_reason("blocked-human", None) == "blocked"
        assert csn.degraded_reason("blocked-human", "login") == "logged-out"
        assert csn.degraded_reason("blocked-human", "fable-limit-unfocused") == "usage-limit"
        assert csn.degraded_reason("hung", None) == "hung"
        assert csn.degraded_reason("idle-ready", None) is None
        assert csn.degraded_reason("blocked-known", None) is None


if __name__ == "__main__":
    test_no_supervisor_file_does_nothing()
    test_degraded_notices_once_per_room_with_cooldown()
    test_failed_send_burns_nothing_and_retries()
    test_reason_change_renotices_inside_cooldown()
    test_recovery_announced_once_and_flap_stays_bounded()
    test_alternating_reasons_bounded_per_reason()
    test_v1_ledger_resets_cleanly()
    test_no_verdict_inputs_do_nothing()
    test_unrecognized_state_does_not_fake_recovery()
    test_kill_switch_env_disables_everything()
    test_bodies_are_fixed_strings_never_file_content()
    test_read_core_state_bounds_and_shapes()
    print("ALL PASS")
