#!/usr/bin/env python3
"""The Stop hook's block decision must be parseable JSON.

`src/check-pending-tasks.sh` built its response by hand, and the `\n` in the
printf FORMAT string was emitted as a raw newline inside a JSON string value.
Raw control characters are illegal there, so the client could not parse the
response and printed `stop hook error` instead. The guard exists to stop the
agent going idle with unanswered tasks; because every block decision was
unparseable, it had never once fired.

The empty-queue path returns `{}` and always parsed, which is why nothing
caught this: the failure appears ONLY when the hook has something to say. So
this asserts against a NON-EMPTY queue, and pins that the block actually fires
(a fixture that produced `{}` would pass while proving nothing).

Also pins body fidelity. The old escaping ran `sed 's/"/\\"/g' | tr '\n' ' '`,
which left backslashes unescaped — a task body containing one would break the
JSON again by a different route.

Run: python3 tests/stop-hook-emits-valid-json.test.py
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import tempfile

HOOK = pathlib.Path(__file__).resolve().parent.parent / "src" / "check-pending-tasks.sh"
RESOLVE = 'WORKSPACE="$(bash "$REPO_DIR/scripts/sutando-config.sh" workspace 2>/dev/null)"'

BODY = 'a body with "quotes", a backslash \\ and\na second line\n'


def _run(workspace: pathlib.Path) -> str:
    """Run the real hook against `workspace`, pinning its resolver to it."""
    src = HOOK.read_text()
    assert RESOLVE in src, "workspace resolution line moved; update this test"
    stub = workspace / "hook.sh"
    stub.write_text(src.replace(RESOLVE, f'WORKSPACE="{workspace}"'))
    out = subprocess.run(
        ["bash", str(stub)], capture_output=True, text=True, stdin=subprocess.DEVNULL
    )
    assert out.returncode == 0, f"hook exited {out.returncode}: {out.stderr}"
    return out.stdout


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = pathlib.Path(tmp)
        (ws / "tasks").mkdir()
        (ws / "results").mkdir()

        # Empty queue: the path that always parsed, kept so a regression here is visible too.
        assert json.loads(_run(ws)) == {}, "empty queue must emit {}"

        (ws / "tasks" / "task-1.txt").write_text(f"id: task-1\ntask: {BODY}")
        raw = _run(ws)

        decision = json.loads(raw)  # the assertion: unparseable output fails here
        assert decision["decision"] == "block", (
            f"fixture did not make the guard fire: {decision!r}"
        )

        ctx = decision["additionalContext"]
        for label, needle in (("quotes", '"quotes"'), ("backslash", "\\"), ("newline", "\n")):
            assert needle in ctx, f"task body lost its {label}"

    print("stop-hook-emits-valid-json: PASS")


if __name__ == "__main__":
    main()
