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
import json
import os
import pathlib
import subprocess
import sys
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


def test_an_archived_result_is_not_this_turns_message() -> None:
    """A delivered result is archived within seconds, so it belongs to an earlier
    boundary — and a turn that replied long ago then went silent is the case."""
    with tempfile.TemporaryDirectory() as tmp:
        ws = _workspace(tmp)
        past = time.time() - 5
        archive = ws / "results" / "archive" / "2026-09"
        archive.mkdir(parents=True)
        (archive / "task-1.txt").write_text("a delivered answer\n")
        check("an archived result is not counted",
              turn_ledger.delivery_after(past, ws) is None,
              repr(turn_ledger.delivery_after(past, ws)))
        (ws / "results" / "task-2.txt").write_text("a live answer\n")
        check("a top-level result is counted",
              (turn_ledger.delivery_after(past, ws) or {}).get("target") == "task-2.txt",
              repr(turn_ledger.delivery_after(past, ws)))


def test_hook_is_inert_until_the_ledger_exists() -> None:
    """The arming rule, and the reason the sibling suites stay green."""
    with tempfile.TemporaryDirectory() as tmp:
        ws = _workspace(tmp)
        check("no ledger: an empty queue still emits {}", _hook(ws) == {}, repr(_hook(ws)))
        check("no ledger: a second stop is still quiet", _hook(ws) == {}, repr(_hook(ws)))


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
        test_an_archived_result_is_not_this_turns_message,
        test_hook_is_inert_until_the_ledger_exists,
        test_hook_blocks_a_silent_turn,
        test_a_recorded_send_lets_the_turn_end,
        test_a_recorded_no_send_lets_the_turn_end,
        test_the_result_file_surface,
        test_the_boundary_advances,
        test_an_unanswered_task_still_blocks_with_the_task_reason,
        test_a_gate_that_cannot_run_fails_open,
        test_room_ops_records_only_a_successful_say,
    ):
        print(f"{fn.__name__}:")
        fn()
    if FAILURES:
        print(f"turn-ledger-stop-gate: FAIL ({len(FAILURES)}): {', '.join(FAILURES)}")
        return 1
    print("turn-ledger-stop-gate: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
