#!/usr/bin/env python3
r"""A long outage must not crash the seat: the backoff cap is applied BEFORE the power.

`min(2.0 ** n, RETRY_MAX_S)` evaluates the unbounded exponential first, so the
1024th consecutive failure raises OverflowError and the seat exits. The
entrypoint treats that exit as fatal and stops the container's gateway, so one
provider outage long enough to reach ~17h at the default 60s cap takes down
work unrelated to the failing task.

Both cases drive the module's REAL `main()` loop with a fake clock, never a copy
of its backoff decision.
"""
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEAT = ROOT / "deploy" / "cloud-worker" / "seat-ag2-assistant.py"

FAILS = []


def check(ok, msg):
    print(("  ok   " if ok else "  FAIL ") + msg)
    if not ok:
        FAILS.append(msg)


class Clock:
    """Advances far enough per scan that every retry_at deadline has passed."""

    def __init__(self):
        self.t = 1_000_000.0

    def time(self):
        return self.t

    def sleep(self, _s):
        self.t += 3600.0


def load_seat(tmp):
    (tmp / "tasks").mkdir()
    (tmp / "tasks" / "task-B1.txt").write_text("id: task-B1\ntask: hello\n")
    os.environ["SUTANDO_CLOUD_WORKSPACE"] = str(tmp)
    os.environ["SUTANDO_WORKER_ID"] = "cloud-b"
    os.environ["SUTANDO_STUB_SCAN_S"] = "0.0"
    spec = importlib.util.spec_from_file_location(f"seat_backoff_{tmp.name}", SEAT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.time = Clock()
    m._STOP = False
    return m


def run_case(label, target, recover_at):
    """Fail `target` times (recovering at `recover_at` if set); return (rc, exc, wrote)."""
    tmp = Path(tempfile.mkdtemp(prefix="seat-backoff-"))
    m = load_seat(tmp)
    calls = []

    def answer(task, results, *a, **k):
        calls.append(task.name)
        if recover_at is not None and len(calls) >= recover_at:
            body = "recovered\n\n— cloud-b (ag2-assistant)\n"
            results.mkdir(parents=True, exist_ok=True)
            (results / task.name).write_text(body)
            m._STOP = True
            return body
        if len(calls) >= target:
            m._STOP = True
        return None

    m.answer = answer
    rc, exc = None, None
    try:
        rc = m.main()
    except BaseException as e:  # noqa: BLE001 — the defect IS an escaping exception
        exc = e
    wrote = (tmp / "results" / "task-B1.txt").exists()
    print(f"  [{label}] attempts={len(calls)} rc={rc} exc={type(exc).__name__ if exc else None} results={int(wrote)}")
    return rc, exc, wrote


def main() -> int:
    # 1024 is where 2.0 ** n stops being representable.
    rc, exc, wrote = run_case("long outage", target=1100, recover_at=None)
    check(exc is None, f"1100 consecutive failures do not raise (got {type(exc).__name__ if exc else None})")
    check(rc == 0, f"main() returns 0 after a long outage (got {rc})")
    check(not wrote, "a failing turn still writes no result file")

    rc, exc, wrote = run_case("recovery past 1024", target=1200, recover_at=1050)
    check(exc is None, f"recovery past attempt 1024 does not raise (got {type(exc).__name__ if exc else None})")
    check(rc == 0, f"main() returns 0 after recovering (got {rc})")
    check(wrote, "the recovered answer is written")

    print(("FAIL " + str(len(FAILS))) if FAILS else "PASS")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
