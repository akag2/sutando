#!/usr/bin/env python3
"""A turn must end in a message or an explicit no-send.

THE GAP. `src/check-pending-tasks.sh` walks `<workspace>/tasks/` and requires a
ready `results/<same name>`, so it only ever sees a turn that ANSWERS A QUEUED
TASK. A proactive turn, or one that replies through `room_ops.py say`, has no
task file — and `say` writes nothing to disk — so the hook saw an empty queue,
emitted `{}`, and the agent went idle having said nothing to anybody. The owner's
rule ("the end of a turn must be a msg or no-send") had no enforcement surface at
all, because no surface knew whether a message had happened.

WHAT IS PINNED, AND WHY EACH CASE EARNS ITS PLACE:

  • The two evidence surfaces separately (a recorded send; a READY result file),
    because either one alone would pass a hook that only implemented the other.
  • An UNREADY result file must NOT count. A hook that accepted any file in
    `results/` would pass the empty-result case the sibling suite exists to
    reject, and readiness has one owner (`delivery.readiness`).
  • Both directions of the arming rule. "Inert with no ledger" is what keeps the
    three quiet cases in `check-pending-tasks-workspace.test.sh` quiet, so it is
    a contract, not an accident — and a gate that were inert in BOTH states would
    satisfy it while enforcing nothing, which is why the armed block is pinned
    beside it.
  • The boundary ADVANCES. Without `mark_stop` the gate would pass forever on one
    ancient send; the two-stops-in-a-row case is the only one that can tell a
    live boundary from a frozen one.
  • Task-block precedence, so the new gate cannot mask the older reason.
  • Fail-open when the gate itself cannot run. A Stop gate that fails closed
    wedges the agent into a turn it has no action left to end.

Run: python3 tests/turn-ledger-stop-gate.test.py
"""
from __future__ import annotations

import importlib.util
import datetime
import io
import contextlib
import json
import os
import pathlib
import subprocess
import sys
from unittest import mock
import tempfile
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
LEDGER_CLI = REPO / "src" / "turn_ledger.py"
ROOM_OPS = REPO / "skills" / "agent-room-ops"

sys.path.insert(0, str(REPO / "src"))
import turn_ledger  # noqa: E402


def _load_sibling_stub():
    """Reuse `_stub` from the sibling suite rather than copying its pinning.

    `_stub` pins REPO_DIR as well as the workspace: run from a temp dir,
    `dirname $0/..` points outside the repo, `sutando-config.sh` is never found,
    and the interpreter cascade silently falls back to PATH — the test would then
    measure the fallback instead of the contract. Its assertions on the hook's
    layout also fail loudly here if the hook is restructured.
    """
    path = REPO / "tests" / "stop-hook-emits-valid-json.test.py"
    spec = importlib.util.spec_from_file_location("_stop_hook_sibling", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._stub


_stub = _load_sibling_stub()

FAILURES: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}\n     {detail}")
        FAILURES.append(label)


def _workspace(tmp: str) -> pathlib.Path:
    ws = pathlib.Path(tmp)
    (ws / "tasks").mkdir(exist_ok=True)
    (ws / "results").mkdir(exist_ok=True)
    return ws


def _hook(ws: pathlib.Path, repo_dir: pathlib.Path | None = None) -> dict:
    """Run the real hook against `ws`; return its decision ({} = may stop)."""
    stub = _stub(ws)
    if repo_dir is not None:
        stub.write_text(stub.read_text().replace(f'REPO_DIR="{REPO}"',
                                                 f'REPO_DIR="{repo_dir}"'))
    out = subprocess.run(["/bin/bash", str(stub)], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL)
    assert out.returncode == 0, f"hook exited {out.returncode}: {out.stderr}"
    return json.loads(out.stdout or "{}")


def _cli(ws: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(LEDGER_CLI), "--workspace", str(ws), *args],
                          capture_output=True, text=True)


def _arm(ws: pathlib.Path) -> None:
    """Give the install its first recorded action, which is what arms the gate."""
    turn_ledger.record_no_send("arming the gate for this test", ws)


def _blocked(decision: dict) -> bool:
    return decision.get("decision") == "block"


def test_module_records_both_kinds() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = _workspace(tmp)
        turn_ledger.record_send("room", "!abc:ag2.space", ws)
        turn_ledger.record_no_send("nothing worth saying", ws)
        lines = turn_ledger.ledger_path(ws).read_text().splitlines()
        entries = [json.loads(x) for x in lines]
        check("a send records kind + target",
              entries[0]["kind"] == "room" and entries[0]["target"] == "!abc:ag2.space",
              repr(entries[0]))
        check("a no-send records its reason",
              entries[1]["kind"] == "no-send" and entries[1]["reason"] == "nothing worth saying",
              repr(entries[1]))
        check("every entry carries a numeric ts",
              all(isinstance(e["ts"], float) for e in entries), repr(entries))

        now = time.time()
        check("last_action_after returns the newest entry",
              turn_ledger.last_action_after(0, ws)["kind"] == "no-send",
              repr(turn_ledger.last_action_after(0, ws)))
        check("last_action_after is None when nothing is newer",
              turn_ledger.last_action_after(now + 60, ws) is None,
              repr(turn_ledger.last_action_after(now + 60, ws)))


def test_the_file_is_bounded_and_keeps_the_newest() -> None:
    """Past the cap, the oldest go and every surviving line still parses.

    The trim seeks to a byte offset, which lands mid-line; a scanner that kept
    that fragment would leave an unparseable first line in a file whose whole
    purpose is to be read back.
    """
    with tempfile.TemporaryDirectory() as tmp:
        ws = _workspace(tmp)
        for i in range(4000):
            turn_ledger.record_send("room", f"!room-{i:06d}:ag2.space", ws)
        size = turn_ledger.ledger_path(ws).stat().st_size
        raw = turn_ledger.ledger_path(ws).read_text().splitlines()
        entries = turn_ledger.read_entries(ws)
        check("the ledger stays under the cap", size <= turn_ledger.MAX_BYTES,
              f"{size} bytes > {turn_ledger.MAX_BYTES}")
        # What survives must be a contiguous NEWEST suffix. "the last entry is
        # last" is true of any trim direction — the newest append lands at the end.
        kept = [int(e["target"][6:12]) for e in entries]
        check("what survives is a contiguous suffix of the newest",
              kept and kept[0] > 0 and kept == list(range(kept[0], 4000)),
              f"kept {len(kept)} entries, first={kept[0] if kept else None}, "
              f"contiguous={kept == list(range(kept[0], 4000)) if kept else False}")
        check("no half-line survived the trim", len(entries) == len(raw),
              f"{len(raw)} lines but only {len(entries)} parsed")


def test_concurrent_writers_do_not_lose_or_interleave_a_line() -> None:
    """Through the production writer, per the shared-record rule in CLAUDE.md.

    Several workers can record in the same window. O_APPEND plus one `write()`
    per line is what makes that safe; a read-modify-write would silently drop
    whichever writer read first, and the loss is invisible in the file — it just
    holds fewer lines than were recorded.
    """
    workers, per_worker = 8, 60
    with tempfile.TemporaryDirectory() as tmp:
        ws = _workspace(tmp)
        code = ("import sys; sys.path.insert(0, sys.argv[1])\n"
                "import turn_ledger\n"
                "for i in range(%d): turn_ledger.record_send('room', sys.argv[3] + '-%%03d' %% i, sys.argv[2])\n"
                % per_worker)
        procs = [subprocess.Popen([sys.executable, "-c", code, str(REPO / "src"), str(ws), f"w{p}"])
                 for p in range(workers)]
        for proc in procs:
            proc.wait()
        raw = [ln for ln in turn_ledger.ledger_path(ws).read_text().splitlines() if ln.strip()]
        targets = {e["target"] for e in turn_ledger.read_entries(ws)}
        expected = {f"w{p}-{i:03d}" for p in range(workers) for i in range(per_worker)}
        check("no concurrent write was lost", targets == expected,
              f"{len(targets)} of {len(expected)} recorded; missing {len(expected - targets)}")
        check("no line was interleaved", len(raw) == len(expected),
              f"{len(raw)} raw lines for {len(expected)} records")


def test_an_archived_result_is_dated_not_dismissed() -> None:
    """Archival does not establish a prior turn boundary.

    The bridge archives a delivered result within seconds, independently of the
    turn ending, so a reply made THIS turn is routinely archived before Stop
    runs. Treating "archived" as "old" therefore blocks turns that did answer.
    The boundary is the timestamp, not the directory.
    """
    with tempfile.TemporaryDirectory() as tmp:
        ws = _workspace(tmp)
        month = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")
        archive = ws / "results" / "archive" / month
        archive.mkdir(parents=True)

        boundary = time.time() - 5
        fresh = archive / "task-this-turn.txt"
        fresh.write_text("a delivered answer\n")
        check("an archived result newer than the boundary IS this turn's message",
              turn_ledger.delivery_after(boundary, ws) is not None,
              repr(turn_ledger.delivery_after(boundary, ws)))

        stale = archive / "task-last-turn.txt"
        stale.write_text("an older answer\n")
        os.utime(stale, (boundary - 60, boundary - 60))
        fresh.unlink()
        check("an archived result older than the boundary is not",
              turn_ledger.delivery_after(boundary, ws) is None,
              repr(turn_ledger.delivery_after(boundary, ws)))


def test_an_absent_ledger_is_nothing_sent_not_unjudgeable() -> None:
    """There is no arming rule, because arming defeated the gate.

    It previously allowed any turn while the ledger file was absent, and only a
    recorded send creates that file — so a turn that never sends never created
    the thing that would catch it. Measured on the live host: five consecutive
    silent turns all passed. Writing a result file does not create it either,
    which is how most replies are made here, so it would have stayed inert
    indefinitely while looking installed.

    Only the FIRST stop is unjudgeable: there is no boundary to measure from.
    """
    with tempfile.TemporaryDirectory() as tmp:
        ws = _workspace(tmp)
        # `check`'s detail argument is eager, so an inline `repr(_hook(ws))` runs
        # the hook again and spends this turn's one reminder before the assertion.
        first = _hook(ws)
        check("first ever stop is allowed (no boundary yet)", first == {}, repr(first))
        second = _hook(ws)
        check("a second silent turn is reminded", second != {}, repr(second))
        third = _hook(ws)
        check("the same turn is not reminded twice", third == {}, repr(third))


def test_hook_blocks_a_silent_turn() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = _workspace(tmp)
        _arm(ws)
        _hook(ws)  # consumes the arming entry and sets the boundary
        decision = _hook(ws)
        check("armed + nothing said: the turn is blocked", _blocked(decision), repr(decision))
        check("the block says why",
              "no-send" in json.dumps(decision) and "message" in decision["reason"],
              repr(decision))


def test_a_recorded_send_lets_the_turn_end() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = _workspace(tmp)
        _arm(ws)
        _hook(ws)
        turn_ledger.record_send("room", "!r:ag2.space", ws)
        check("a send since the last stop ends the turn", _hook(ws) == {}, repr(_hook(ws)))


def test_a_recorded_no_send_lets_the_turn_end() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = _workspace(tmp)
        _arm(ws)
        _hook(ws)
        res = _cli(ws, "no-send", "read-only turn, nothing to report")
        check("the no-send CLI exits 0", res.returncode == 0, res.stderr)
        check("an explicit no-send ends the turn", _hook(ws) == {}, repr(_hook(ws)))


def test_the_result_file_surface() -> None:
    """The other way a reply leaves this agent — and readiness still decides."""
    with tempfile.TemporaryDirectory() as tmp:
        ws = _workspace(tmp)
        _arm(ws)
        _hook(ws)
        (ws / "results" / "proactive-1.txt").write_text("   \n\t\n")
        decision = _hook(ws)
        check("a whitespace-only result is not a message", _blocked(decision), repr(decision))
        (ws / "results" / "proactive-1.txt").write_text("here is the thing I found\n")
        check("a ready result ends the turn", _hook(ws) == {}, repr(_hook(ws)))


def test_the_boundary_advances() -> None:
    """One send must license exactly one turn ending, not every future one."""
    with tempfile.TemporaryDirectory() as tmp:
        ws = _workspace(tmp)
        _arm(ws)
        _hook(ws)
        turn_ledger.record_send("room", "!r:ag2.space", ws)
        first = _hook(ws)
        second = _hook(ws)
        check("the turn that spoke may end", first == {}, repr(first))
        check("the next turn cannot reuse that send", _blocked(second), repr(second))


def test_an_unanswered_task_still_blocks_with_the_task_reason() -> None:
    """Precedence: the new gate must not mask the older, more specific reason."""
    with tempfile.TemporaryDirectory() as tmp:
        ws = _workspace(tmp)
        _arm(ws)
        (ws / "tasks" / "task-1.txt").write_text("id: task-1\ntask: answer me\n")
        decision = _hook(ws)
        check("an unanswered task still blocks", _blocked(decision), repr(decision))
        check("and it is still the task reason",
              decision["reason"] == "Unprocessed tasks in tasks/", repr(decision))
        check("naming the task", "task-1.txt" in decision["additionalContext"],
              repr(decision)[:200])


def test_a_gate_that_cannot_run_fails_open() -> None:
    """A crashing gate exits nonzero with nothing on stdout — the same shape as a
    refusal minus the reason. It must let the turn end: a Stop gate that fails
    closed leaves the agent no action that clears it."""
    with tempfile.TemporaryDirectory() as tmp:
        ws = _workspace(tmp)
        _arm(ws)
        _hook(ws)
        check("control: this fixture blocks with the real gate", _blocked(_hook(ws)),
              "the fail-open case below would prove nothing")

        shadow = pathlib.Path(tmp) / "shadow-repo"
        (shadow / "src").mkdir(parents=True)
        (shadow / "scripts").symlink_to(REPO / "scripts")
        (shadow / "src" / "delivery").symlink_to(REPO / "src" / "delivery")
        (shadow / "src" / "turn_ledger.py").write_text(
            "import sys\nraise SystemExit(1)\n")
        decision = _hook(ws, repo_dir=shadow)
        check("a broken gate lets the turn end", decision == {}, repr(decision))


def test_room_ops_records_only_a_successful_say() -> None:
    """The wiring, through `room_ops._main` — the real dispatch, not a re-call.

    Run in a subprocess with the workspace pinned through the repo's documented
    test hatch, and the resolved path ASSERTED inside it: an unpinned run would
    write into the caller's live workspace, and this test is the one place that
    exercises resolution rather than passing a path.
    """
    driver = '''
import json, os, pathlib, sys
from unittest import mock
sys.path.insert(0, os.environ["ROOM_OPS"])
sys.path.insert(0, os.environ["SRC"])
import turn_ledger, room_ops
ws = pathlib.Path(os.environ["WS"]).resolve()
resolved = turn_ledger.ledger_path().resolve()
assert resolved == ws / "state" / turn_ledger.LEDGER_NAME, f"unpinned: {resolved}"
for ok, eid in ((True, "$evt"), (True, None), (False, None)):
    res = {"ok": ok, "room_id": "!r:ag2.space", "event_id": eid}
    with mock.patch.object(room_ops._say, "say", return_value=res):
        room_ops._main(["say", "!r:ag2.space", "hello"])
print(json.dumps(turn_ledger.read_entries()))
'''
    with tempfile.TemporaryDirectory() as tmp:
        ws = _workspace(tmp)
        env = dict(os.environ, SUTANDO_TEST_MODE="1", SUTANDO_WORKSPACE=str(ws),
                   ROOM_OPS=str(ROOM_OPS), SRC=str(REPO / "src"), WS=str(ws))
        out = subprocess.run([sys.executable, "-c", driver], capture_output=True,
                             text=True, env=env)
        if out.returncode != 0:
            check("room_ops driver ran", False, out.stderr[-600:])
            return
        entries = json.loads(out.stdout.strip().splitlines()[-1])
        check("a confirmed say is recorded",
              len(entries) >= 1 and entries[0] == {**entries[0], "kind": "room",
                                                   "target": "!r:ag2.space"},
              repr(entries))
        check("an unconfirmed (ok, no event id) say is recorded too",
              len(entries) == 2, f"expected 2 entries, got {len(entries)}: {entries}")
        check("a failed say records nothing",
              all(e.get("target") == "!r:ag2.space" for e in entries) and len(entries) == 2,
              repr(entries))


def main() -> int:
    for fn in (
        test_module_records_both_kinds,
        test_the_file_is_bounded_and_keeps_the_newest,
        test_concurrent_writers_do_not_lose_or_interleave_a_line,
        test_an_archived_result_is_dated_not_dismissed,
        test_an_absent_ledger_is_nothing_sent_not_unjudgeable,
        test_hook_blocks_a_silent_turn,
        test_a_recorded_send_lets_the_turn_end,
        test_a_recorded_no_send_lets_the_turn_end,
        test_the_result_file_surface,
        test_the_boundary_advances,
        test_an_unanswered_task_still_blocks_with_the_task_reason,
        test_a_gate_that_cannot_run_fails_open,
        test_room_ops_records_only_a_successful_say,
        test_an_absent_ledger_still_blocks_after_the_first_stop,
        test_the_command_line_surface,
        test_one_reminder_per_turn_not_a_standing_refusal,
        test_ended_on_a_message_versus_sent_then_went_quiet,
        test_turn_start_is_reachable_from_the_command_line,
        test_record_say_contract_in_process,
        test_a_result_older_than_the_boundary_is_not_this_turns,
        test_bookkeeping_never_raises_into_the_send_path,
    ):
        print(f"{fn.__name__}:")
        fn()
    if FAILURES:
        print(f"turn-ledger-stop-gate: FAIL ({len(FAILURES)}): {', '.join(FAILURES)}")
        return 1
    print("turn-ledger-stop-gate: PASS")
    return 0


def test_an_absent_ledger_still_blocks_after_the_first_stop():
    """A turn that never sends anything never creates the ledger — so treating an
    absent ledger as unjudgeable made the gate inert for exactly the case it
    exists to catch. Only a missing boundary (the first stop on a fresh install)
    is genuinely unjudgeable.

    Verified against the live deployment before the fix: with no ledger the gate
    passed on every consecutive turn.
    """
    with tempfile.TemporaryDirectory() as tmp:
        ws = pathlib.Path(tmp)
        (ws / "state").mkdir()
        assert turn_ledger.stop_gate(ws) is None, "the first stop has no boundary to measure from"

        # Production resets each turn via the UserPromptSubmit hook, so a test
        # spanning turns must too, or it measures one turn refused twice.
        turn_ledger.begin_turn(ws)
        assert turn_ledger.stop_gate(ws) is not None, (
            "a silent turn is reminded even though no ledger exists"
        )

        turn_ledger.begin_turn(ws)
        turn_ledger.record_send("room", "!r:example.org", workspace=ws)
        assert turn_ledger.stop_gate(ws) is None, "a recorded send lets the turn end"

        turn_ledger.begin_turn(ws)
        assert turn_ledger.stop_gate(ws) is not None, "the next silent turn is reminded again"


def test_the_command_line_surface() -> None:
    """The hook shells out to this, so its argv handling is production code.

    Covers each verb, `--workspace` stripping, and the usage path — a wrong exit
    code here is indistinguishable from a verdict, and the hook only treats
    `rc == 1` with a reason as a refusal.
    """
    with tempfile.TemporaryDirectory() as tmp:
        ws = _workspace(tmp)
        buf = io.StringIO()

        with contextlib.redirect_stdout(buf):
            first = turn_ledger.main(["--workspace", str(ws), "stop-gate"])
        check("first stop allows (rc 0)", first == 0, repr(first))

        with contextlib.redirect_stdout(buf):
            second = turn_ledger.main(["--workspace", str(ws), "stop-gate"])
        check("a silent second stop refuses (rc 1)", second == 1, repr(second))
        check("the refusal prints a reason", "no-send" in buf.getvalue(), repr(buf.getvalue()[:80]))

        rc = turn_ledger.main(["--workspace", str(ws), "send", "room", "!r:example.org"])
        check("send records and exits 0", rc == 0, repr(rc))
        check("send reached the ledger",
              any(e["target"] == "!r:example.org" for e in turn_ledger.read_entries(ws)),
              repr(turn_ledger.read_entries(ws)[-1:]))

        with contextlib.redirect_stdout(buf):
            check("a recorded send lets the turn end",
                  turn_ledger.main(["--workspace", str(ws), "stop-gate"]) == 0, "")

        rc = turn_ledger.main(["--workspace", str(ws), "no-send", "nothing", "to", "say"])
        check("no-send records and exits 0", rc == 0, repr(rc))
        check("no-send keeps its whole reason",
              any(e.get("reason") == "nothing to say" for e in turn_ledger.read_entries(ws)),
              repr(turn_ledger.read_entries(ws)[-1:]))

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            unknown = turn_ledger.main(["--workspace", str(ws), "wat"])
        check("an unknown verb is rc 2, never a verdict", unknown == 2, repr(unknown))
        check("usage goes to stderr", "usage:" in err.getvalue(), repr(err.getvalue()[:60]))


def test_a_result_older_than_the_boundary_is_not_this_turns() -> None:
    """The filters inside the result scan: too old, and present but not ready."""
    with tempfile.TemporaryDirectory() as tmp:
        ws = _workspace(tmp)
        (ws / "results").mkdir(parents=True, exist_ok=True)
        boundary = time.time() - 5

        stale = ws / "results" / "task-old.txt"
        stale.write_text("an older answer\n")
        os.utime(stale, (boundary - 60, boundary - 60))
        check("a result older than the boundary is skipped",
              turn_ledger.delivery_after(boundary, ws) is None,
              repr(turn_ledger.delivery_after(boundary, ws)))

        (ws / "results" / "task-empty.txt").write_text("   \n")
        check("a fresh but unready result is not a message",
              turn_ledger.delivery_after(boundary, ws) is None,
              repr(turn_ledger.delivery_after(boundary, ws)))


def test_bookkeeping_never_raises_into_the_send_path() -> None:
    """Recording happens after a message has already gone out, so a failure here
    must never propagate — the caller has nothing left to undo.

    Exercises the failure branches directly: an unwritable state directory, and
    a ledger holding a malformed line beside a good one.
    """
    with tempfile.TemporaryDirectory() as tmp:
        ws = _workspace(tmp)
        turn_ledger.record_send("room", "!good:example.org", workspace=ws)

        # A corrupt line must be skipped, not crash the reader or hide the rest.
        with open(turn_ledger.ledger_path(ws), "a", encoding="utf-8") as fh:
            fh.write("\n")               # a blank line is skipped, not parsed
            fh.write("not json at all\n")
            fh.write(json.dumps({"no_ts": True}) + "\n")
        entries = turn_ledger.read_entries(ws)
        check("a malformed line is skipped, the good one survives",
              any(e.get("target") == "!good:example.org" for e in entries),
              repr(entries))

        # A state dir that was never writable: the lock file cannot even be
        # created, which is a different failure from an existing-but-locked one.
        with tempfile.TemporaryDirectory() as tmp2:
            fresh = _workspace(tmp2)
            (fresh / "state").mkdir(parents=True, exist_ok=True)
            os.chmod(fresh / "state", 0o500)
            try:
                turn_ledger.record_send("room", "!never:example.org", workspace=fresh)
                check("a state dir that was never writable does not raise", True, "")
            finally:
                os.chmod(fresh / "state", 0o700)

        state = ws / "state"
        mode = state.stat().st_mode
        os.chmod(state, 0o500)
        try:
            turn_ledger.record_send("room", "!blocked:example.org", workspace=ws)
            turn_ledger.record_no_send("also blocked", workspace=ws)
            check("an unwritable state dir does not raise", True, "")
            check("the gate still answers rather than exploding",
                  turn_ledger.stop_gate(ws) in (None,) or isinstance(turn_ledger.stop_gate(ws), str), "")
        finally:
            os.chmod(state, mode)


def test_record_say_contract_in_process() -> None:
    """`_record_say`'s own contract, called directly.

    The sibling test drives the real dispatch in a SUBPROCESS, which is the right
    shape for the wiring but invisible to coverage run in this process — the added
    lines read as untested. This exercises the same three cases in-process, so the
    contract is measured where it is asserted.
    """
    room_ops_dir = str(REPO / "skills" / "agent-room-ops")
    if room_ops_dir not in sys.path:
        sys.path.insert(0, room_ops_dir)
    import room_ops  # noqa: PLC0415 — imported here so the path insert above applies

    with tempfile.TemporaryDirectory() as tmp:
        ws = _workspace(tmp)
        # Both are required: the resolver honours the pin only in test mode.
        os.environ["SUTANDO_TEST_MODE"] = "1"
        os.environ["SUTANDO_WORKSPACE"] = str(ws)
        assert turn_ledger.ledger_path().resolve() == (
            ws / "state" / turn_ledger.LEDGER_NAME).resolve(), "unpinned — would write live"
        try:
            room_ops._record_say({"ok": True, "room_id": "!confirmed:ag2.space",
                                  "event_id": "$evt"})
            room_ops._record_say({"ok": True, "room_id": "!unconfirmed:ag2.space",
                                  "event_id": None})
            room_ops._record_say({"ok": False, "room_id": "!refused:ag2.space",
                                  "event_id": None})
            room_ops._record_say(None)
            room_ops._record_say("not a dict")
        finally:
            os.environ.pop("SUTANDO_WORKSPACE", None)
            os.environ.pop("SUTANDO_TEST_MODE", None)

        # The dispatch call site, in-process: `_main` is what actually wires the
        # recording to a send, and the sibling test reaches it only in a subprocess.
        os.environ["SUTANDO_TEST_MODE"] = "1"
        os.environ["SUTANDO_WORKSPACE"] = str(ws)
        try:
            with mock.patch.object(room_ops._say, "say",
                                   return_value={"ok": True, "room_id": "!dispatch:ag2.space",
                                                 "event_id": "$e"}):
                room_ops._main(["say", "!dispatch:ag2.space", "hello"])

            # The docstring promises this never raises into the send path. A message
            # has already gone out by then, so there is nothing left to undo.
            with mock.patch.object(turn_ledger, "record_send",
                                   side_effect=RuntimeError("ledger exploded")), \
                 mock.patch.dict(sys.modules, {"turn_ledger": turn_ledger}):
                room_ops._record_say({"ok": True, "room_id": "!boom:ag2.space",
                                      "event_id": "$e"})
            check("a raising ledger does not break the send path", True, "")
        finally:
            os.environ.pop("SUTANDO_WORKSPACE", None)
            os.environ.pop("SUTANDO_TEST_MODE", None)

        targets = [e.get("target") for e in turn_ledger.read_entries(ws)]
        check("the dispatch path records", "!dispatch:ag2.space" in targets, repr(targets))
        check("a confirmed say is recorded", "!confirmed:ag2.space" in targets, repr(targets))
        check("an unconfirmed say is recorded too (fail-open, matching receipt.py)",
              "!unconfirmed:ag2.space" in targets, repr(targets))
        check("a refused say records nothing", "!refused:ag2.space" not in targets, repr(targets))
        check("a non-dict result is ignored rather than raising", True, "")


def test_one_reminder_per_turn_not_a_standing_refusal() -> None:
    """The owner's design: nudge once, then let the turn end.

    A gate that refuses repeatedly turns any mistake into a loop of duplicate
    replies — the failure the reviewer reproduced. Nudging once bounds the cost of
    being wrong to a single wasted prompt.
    """
    with tempfile.TemporaryDirectory() as tmp:
        ws = _workspace(tmp)
        turn_ledger.stop_gate(ws)                      # establish the boundary

        turn_ledger.begin_turn(ws)
        check("a silent turn is reminded once", turn_ledger.stop_gate(ws) is not None, "")
        check("the same turn is NOT refused twice", turn_ledger.stop_gate(ws) is None,
              "a second refusal is how a wrong gate becomes a loop")

        turn_ledger.begin_turn(ws)
        check("the next turn is reminded again", turn_ledger.stop_gate(ws) is not None,
              "the reset must re-arm it")


def test_ended_on_a_message_versus_sent_then_went_quiet() -> None:
    """The distinction the owner corrected me on twice.

    Each case gets its own workspace. A successful `stop_gate` advances the
    boundary past the send, so reusing one would leave the second case with
    nothing after the boundary — it would then be reminded for "nothing sent"
    and pass without ever exercising the elapsed-time rule.
    """
    with tempfile.TemporaryDirectory() as tmp:
        ws = _workspace(tmp)
        turn_ledger.stop_gate(ws)                     # boundary
        turn_ledger.begin_turn(ws)
        turn_ledger.record_send("room", "!r:example.org", workspace=ws)
        check("a message just sent ends the turn cleanly",
              turn_ledger.stop_gate(ws) is None, "")

    with tempfile.TemporaryDirectory() as tmp:
        ws = _workspace(tmp)
        turn_ledger.stop_gate(ws)                     # boundary
        turn_ledger.begin_turn(ws)
        turn_ledger.record_send("room", "!r:example.org", workspace=ws)
        original = turn_ledger.ENDED_ON_A_MESSAGE_S
        try:
            turn_ledger.ENDED_ON_A_MESSAGE_S = 0.0    # the send is now "long ago"
            # The send is still AFTER the boundary, so this measures the elapsed
            # rule rather than an empty window.
            assert turn_ledger.delivery_after(turn_ledger.last_stop_ts(ws), ws) is not None, (
                "setup wrong: nothing after the boundary, so the rule is untested"
            )
            check("a turn that sent early then went quiet IS reminded",
                  turn_ledger.stop_gate(ws) is not None,
                  "this is the case the previous design could not see")
        finally:
            turn_ledger.ENDED_ON_A_MESSAGE_S = original


def test_turn_start_is_reachable_from_the_command_line() -> None:
    """The hook calls this; if the verb is missing the reset silently never happens."""
    with tempfile.TemporaryDirectory() as tmp:
        ws = _workspace(tmp)
        turn_ledger.spend_reminder(ws)
        check("reminder starts spent", turn_ledger.reminder_spent(ws), "")
        rc = turn_ledger.main(["--workspace", str(ws), "turn-start"])
        check("turn-start exits 0", rc == 0, repr(rc))
        check("turn-start clears the mark", not turn_ledger.reminder_spent(ws), "")


if __name__ == "__main__":
    sys.exit(main())
