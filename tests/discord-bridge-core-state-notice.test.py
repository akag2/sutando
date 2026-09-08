#!/usr/bin/env python3
"""discord-bridge core-state notices (silent-core fix) — the async binder.

The shared decision logic (dedup, cooldown, recovery, per-surface ledger) is
covered in packages/ag2-sparrow/tests/test_core_state_notice.py. This covers
only what the Discord binder owns:

  * intake sends a degraded notice to the message's own channel and commits to
    the DISCORD ledger (not the gateway's);
  * intake is a no-op when the core is healthy;
  * the recovery pass resolves ledger channel ids to channels and sends "back
    online", clearing the ledger; an unresolvable channel is dropped, never
    wedged;
  * a send failure at intake burns nothing (retryable next message).

Run: python3 tests/discord-bridge-core-state-notice.test.py
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BRIDGE = REPO / "src" / "discord-bridge.py"
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "packages" / "ag2-sparrow"))

_FAILURES: list[str] = []


def fail(msg: str) -> None:
    _FAILURES.append(msg)
    print(f"FAIL: {msg}", file=sys.stderr)


def expect(cond, label: str) -> None:
    if not cond:
        fail(label)


def _install_discord_stub():
    stub = types.ModuleType("discord")

    class _Intents:
        def __init__(self, *a, **k):
            pass

        @classmethod
        def default(cls):
            return cls()

        def __setattr__(self, k, v):
            object.__setattr__(self, k, v)

    class _Client:
        def __init__(self, *a, **k):
            self.user = None
            self.loop = types.SimpleNamespace(create_task=lambda *a, **k: None)

        def event(self, fn):
            return fn

        def get_channel(self, _id):
            return None

    stub.Intents = _Intents
    stub.Client = _Client
    stub.MessageType = types.SimpleNamespace(default=0, reply=1)
    stub.File = lambda *a, **k: None
    stub.DMChannel = type("_DMChannel", (), {})
    sys.modules["discord"] = stub


def load_bridge(config_root: Path):
    _install_discord_stub()
    env_dir = Path(config_root) / "channels" / "discord"
    env_dir.mkdir(parents=True, exist_ok=True)
    (env_dir / ".env").write_text("DISCORD_BOT_TOKEN=test-stub-token\n")
    src = BRIDGE.read_text()
    spec = importlib.util.spec_from_loader("bridge", loader=None)
    bridge = importlib.util.module_from_spec(spec)
    bridge.__file__ = str(BRIDGE)
    code = compile(src, str(BRIDGE), "exec")
    exec(code, bridge.__dict__)
    return bridge


class _Channel:
    def __init__(self, cid, ok=True):
        self.id = cid
        self.ok = ok
        self.sent: list[str] = []

    async def send(self, body, *a, **k):
        if not self.ok:
            raise RuntimeError("discord send failed")
        self.sent.append(body)


def _write_state(state_dir: Path, state, kind=None):
    (state_dir / "core-supervisor.json").write_text(json.dumps(
        {"state": state, "detail": "t", "prompt": None, "kind": kind}))


def main():
    tmp_home = Path(tempfile.mkdtemp(prefix="dbcsn-home-"))
    prior_cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    os.environ["CLAUDE_CONFIG_DIR"] = str(tmp_home)
    state_dir = Path(tempfile.mkdtemp(prefix="dbcsn-state-"))
    try:
        bridge = load_bridge(tmp_home)
        bridge.STATE_DIR = state_dir  # redirect ledger + supervisor reads
        ledger = state_dir / bridge._CORE_NOTICE_LEDGER

        # 1. degraded intake → notice to this channel, committed to DISCORD ledger
        _write_state(state_dir, "logged-out")
        ch = _Channel(555000000000000001)
        asyncio.run(bridge._core_notice_on_intake(ch))
        expect(len(ch.sent) == 1, "intake: exactly one notice sent")
        expect("logged out" in ch.sent[0], "intake: reason wording is 'logged out'")
        expect("_(automated notice)_" in ch.sent[0], "intake: discord suffix, not gateway's")
        expect("gateway" not in ch.sent[0], "intake: must not carry the gateway suffix")
        expect(ledger.exists(), "intake: wrote the discord-specific ledger")
        expect(not (state_dir / "core-state-notice.json").exists(),
               "intake: did NOT touch the gateway ledger")

        # 2. second message same channel/reason → cooldown suppresses
        ch2 = _Channel(555000000000000001)
        asyncio.run(bridge._core_notice_on_intake(ch2))
        expect(ch2.sent == [], "intake: duplicate suppressed by cooldown")

        # 3. healthy core at intake → no-op
        _write_state(state_dir, "idle-ready")
        ch3 = _Channel(555000000000000002)
        asyncio.run(bridge._core_notice_on_intake(ch3))
        expect(ch3.sent == [], "intake: healthy core sends nothing")

        # 4. recovery pass → the noticed channel gets "back online", ledger clears
        rec_ch = _Channel(555000000000000001)
        bridge.client.get_channel = lambda cid: rec_ch if cid == rec_ch.id else None
        asyncio.run(bridge._core_notice_recovery_once())
        expect(len(rec_ch.sent) == 1 and "back online" in rec_ch.sent[0],
               "recovery: back-online line delivered")
        active = json.loads(ledger.read_text()).get("active", {})
        expect(active == {}, "recovery: ledger active cleared")
        # idempotent: nothing owed now
        rec_ch2 = _Channel(555000000000000001)
        bridge.client.get_channel = lambda cid: rec_ch2
        asyncio.run(bridge._core_notice_recovery_once())
        expect(rec_ch2.sent == [], "recovery: nothing re-sent once cleared")

        # 5. unresolvable channel is dropped from the ledger, never wedged
        _write_state(state_dir, "crashed")
        gone = _Channel(555000000000000009)
        asyncio.run(bridge._core_notice_on_intake(gone))
        expect(len(gone.sent) == 1, "setup: degraded notice for the vanishing channel")
        _write_state(state_dir, "running")
        bridge.client.get_channel = lambda cid: None  # channel no longer resolvable
        asyncio.run(bridge._core_notice_recovery_once())
        active = json.loads(ledger.read_text()).get("active", {})
        expect(active == {}, "recovery: unresolvable channel cleared, not stuck")

        # 6. intake send failure burns nothing (retry next message)
        _write_state(state_dir, "crashed")
        failing = _Channel(555000000000000003, ok=False)
        asyncio.run(bridge._core_notice_on_intake(failing))
        ok = _Channel(555000000000000003)
        asyncio.run(bridge._core_notice_on_intake(ok))
        expect(len(ok.sent) == 1, "intake: retry after a failed send (nothing burned)")
    finally:
        shutil.rmtree(state_dir, ignore_errors=True)
        shutil.rmtree(tmp_home, ignore_errors=True)
        if prior_cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = prior_cfg

    if _FAILURES:
        print(f"\n{len(_FAILURES)} FAILURE(S)")
        sys.exit(1)
    print("PASS: discord-bridge core-state notices (intake + recovery + ledger isolation)")


if __name__ == "__main__":
    main()
