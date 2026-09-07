#!/usr/bin/env python3
r"""A failed turn must leave the task retryable by the SAME seat.

The seat's scan loop used to run `done.add(task.name)` BEFORE attempting the
turn, so `done` recorded attempts rather than answers. Removing the terminal
failure-result write does not cure that on its own: a task whose sidecar was
briefly down would stay pending forever on this seat, and a single-seat
deployment has no other seat to re-front the lease to.

This drives the module's REAL `main()` loop — not a copy of its decision — with
`answer` failing once and then succeeding.
"""
import importlib.util
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEAT = ROOT / "deploy" / "cloud-worker" / "seat-ag2-assistant.py"

FAILS = []
def check(ok, msg):
    print(("  ok   " if ok else "  FAIL ") + msg)
    if not ok:
        FAILS.append(msg)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="seat-retry-"))
    (tmp / "tasks").mkdir()
    (tmp / "tasks" / "task-R1.txt").write_text("id: task-R1\ntask: hello\n")

    os.environ["SUTANDO_CLOUD_WORKSPACE"] = str(tmp)
    os.environ["SUTANDO_WORKER_ID"] = "cloud-t"
    os.environ["SUTANDO_STUB_SCAN_S"] = "0.05"
    spec = importlib.util.spec_from_file_location("seat_retry", SEAT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.RETRY_MAX_S = 0.2          # keep the backoff inside the test's patience

    calls = []

    def flaky_answer(task, results, *a, **k):
        calls.append(task.name)
        if len(calls) == 1:
            return None          # the sidecar is down: no result file written
        body = "recovered\n\n— cloud-t (ag2-assistant)\n"
        (results / task.name).write_text(body)
        return body

    m.answer = flaky_answer
    out = tmp / "results" / "task-R1.txt"

    # main() installs SIGTERM/SIGINT handlers, which only works on the main
    # thread — so the LOOP runs here and the stopper runs on the side.
    def stopper():
        deadline = time.time() + 10
        while time.time() < deadline and not out.exists():
            time.sleep(0.05)
        time.sleep(0.1)
        m._stop()

    t = threading.Thread(target=stopper, daemon=True)
    t.start()
    rc = m.main()
    t.join(timeout=5)

    check(rc == 0, f"main() returned 0 after _stop() (got {rc})")
    check(len(calls) >= 2,
          f"the loop re-attempted the task after the failed turn (calls={len(calls)}) "
          "— exactly 1 is the done-on-attempt defect")
    check(out.exists(), "the recovered turn wrote results/task-R1.txt")
    print("\n" + (f"FAILED ({len(FAILS)})" if FAILS else "PASS — a failed turn stays retryable"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
