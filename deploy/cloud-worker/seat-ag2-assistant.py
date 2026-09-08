#!/usr/bin/env python3
"""ag2-assistant seat runtime: the cheap always-on backup seat.

Same file contract as seat-stub.py — every pending `tasks/task-<id>.txt` is
answered into `results/task-<id>.txt` — but the answer comes from an AG2
Assistant sidecar over ACP (JSON-RPC over WebSocket, `acp-serve`): one session
per task, the task body as the prompt, the agent's message text as the result,
signed `— <worker id> (ag2-assistant)`. Every turn is bounded by
SUTANDO_ACP_TURN_TIMEOUT_S. A timeout or a transport failure writes NOTHING:
the gateway closes the server lease on any delivered result, so a failure text
would be the user's terminal answer and no other seat could be re-fronted. The
task stays pending, this seat retries it under backoff, and an unrecovered seat
lets the lease expire into the stack's documented failover.

The WebSocket transport is the ACP SDK's own (`acp.ws.client`, the package
ag2-assistant imports); the JSON-RPC turn is driven here over its Transport
protocol, so a test can plug an in-process transport in.
"""
from __future__ import annotations

import asyncio
import math
import os
import re
import signal
import sys
import time
from pathlib import Path

# The task-protocol owner decides what a pending filename is; a private copy of
# that rule here is what dropped legal dotted broker ids.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from task_archive import is_pending_task_file  # noqa: E402

WS = Path(os.environ.get("SUTANDO_CLOUD_WORKSPACE") or "/workspace")
WORKER = os.environ.get("SUTANDO_WORKER_ID") or "cloud"
ACP_URL = os.environ.get("AG2ASSISTANT_ACP_URL") or "ws://assistant:8802"
ACP_TOKEN = os.environ.get("AG2ASSISTANT_ACP_TOKEN") or ""
TURN_TIMEOUT_S = float(os.environ.get("SUTANDO_ACP_TURN_TIMEOUT_S") or "300")
SCAN_S = float(os.environ.get("SUTANDO_STUB_SCAN_S") or "1.0")
# A failed turn leaves the task pending, so the scan would re-attempt it every
# SCAN_S. Back off per task instead: 2**attempt seconds, capped.
RETRY_MAX_S = float(os.environ.get("SUTANDO_ACP_RETRY_MAX_S") or "60")
SIGNATURE = f"— {WORKER} (ag2-assistant)"
PROTOCOL_VERSION = 1
_STOP = False


def _stop(*_a) -> None:
    global _STOP
    _STOP = True


def prompt_of(task_text: str) -> str:
    """The `task:` value — the last header, free-form to end of file."""
    lines = task_text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("task:"):
            return "\n".join([line[len("task:"):].strip()] + lines[i + 1:]).strip()
    return task_text.strip()


class AcpTurn:
    """One ACP client turn over a Transport (send(dict) / receive() -> dict|None / close())."""

    def __init__(self, transport) -> None:
        self.t = transport
        self._next_id = 0
        self.text: list[str] = []

    async def call(self, method: str, params: dict) -> dict:
        self._next_id += 1
        rid = self._next_id
        await self.t.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            msg = await self.t.receive()
            if msg is None:
                raise ConnectionError("ACP transport closed mid-turn")
            if msg.get("id") == rid and "method" not in msg:
                if "error" in msg:
                    err = msg["error"] or {}
                    raise RuntimeError(f"ACP {method} failed: {err.get('message') or err}")
                return msg.get("result") or {}
            await self._handle_incoming(msg)

    async def _handle_incoming(self, msg: dict) -> None:
        method = msg.get("method")
        if method == "session/update":
            update = (msg.get("params") or {}).get("update") or {}
            if update.get("sessionUpdate") == "agent_message_chunk":
                content = update.get("content") or {}
                if content.get("type") == "text":
                    self.text.append(content.get("text") or "")
            return
        if msg.get("id") is None:
            return  # some other notification
        # A server request. Permissions are owner-side in ag2-assistant; deny anything that arrives here.
        if method == "session/request_permission":
            await self.t.send({"jsonrpc": "2.0", "id": msg["id"],
                               "result": {"outcome": {"outcome": "cancelled"}}})
            return
        await self.t.send({"jsonrpc": "2.0", "id": msg["id"],
                           "error": {"code": -32601, "message": f"unsupported client method {method}"}})

    async def run(self, prompt: str, cwd: str) -> tuple[str, str]:
        await self.call("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                                   "terminal": False},
            "clientInfo": {"name": "sutando-cloud-worker", "version": "1"},
        })
        session = await self.call("session/new", {"cwd": cwd, "mcpServers": []})
        sid = session.get("sessionId") or ""
        resp = await self.call("session/prompt", {
            "sessionId": sid, "prompt": [{"type": "text", "text": prompt}]})
        return "".join(self.text).strip(), str(resp.get("stopReason") or "")


async def ws_transport():
    from acp.ws.client import create_websocket_stream  # SDK; needs the [http] extra
    headers = {"Authorization": f"Bearer {ACP_TOKEN}"} if ACP_TOKEN else {}
    return await create_websocket_stream(ACP_URL, headers=headers)


async def _connect(transport_factory, deadline: float):
    """Retry the dial until the budget runs out: the sidecar may still be booting."""
    delay = 1.0
    while True:
        try:
            return await transport_factory()
        except Exception:  # noqa: BLE001 — connect errors are the retry case
            if asyncio.get_running_loop().time() + delay >= deadline:
                raise
            await asyncio.sleep(delay)
            delay = min(delay * 2, 15.0)


async def turn(prompt: str, transport_factory, timeout_s: float) -> str | None:
    """The agent's answer, or None when the turn failed — never an exception.

    None is not "empty answer": it means nothing may be written to results/,
    because a written result closes the lease and forecloses failover.
    """
    transport = None
    try:
        async def _run():
            nonlocal transport
            deadline = asyncio.get_running_loop().time() + timeout_s
            transport = await _connect(transport_factory, deadline)
            return await AcpTurn(transport).run(prompt, str(WS))
        text, stop = await asyncio.wait_for(_run(), timeout_s)
        return text or f"(ag2-assistant returned no text; stop reason: {stop or 'unknown'})"
    except asyncio.TimeoutError:
        print(f"seat-ag2-assistant: no answer within {timeout_s:.0f}s — leaving the task pending",
              file=sys.stderr, flush=True)
        return None
    except Exception as exc:  # noqa: BLE001 — a failure must not become an answer
        print(f"seat-ag2-assistant: turn failed ({type(exc).__name__}: {exc}) — leaving the task pending",
              file=sys.stderr, flush=True)
        return None
    finally:
        if transport is not None:
            try:
                await transport.close()
            except Exception:  # noqa: BLE001
                pass


def answer(task: Path, results: Path, transport_factory=ws_transport,
           timeout_s: float = TURN_TIMEOUT_S) -> str | None:
    out = results / task.name
    if out.exists():
        return None
    text = asyncio.run(turn(prompt_of(task.read_text(encoding="utf-8")), transport_factory, timeout_s))
    if text is None:
        return None            # failed turn: no result file, so the lease can expire
    body = f"{text.rstrip()}\n\n{SIGNATURE}\n"
    tmp = out.with_name(out.name + f".{os.getpid()}.tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, out)
    return body


def _backoff_delay(n: int) -> float:
    """2**n seconds capped at RETRY_MAX_S, with the cap applied BEFORE the power.

    Evaluating 2.0 ** n first raises OverflowError at n >= 1024, which exits the
    seat; the entrypoint treats that exit as fatal and stops the gateway too.
    """
    if RETRY_MAX_S <= 0.0:
        return 0.0
    return RETRY_MAX_S if n >= math.log2(RETRY_MAX_S) else 2.0 ** n


def main() -> int:
    tasks, results = WS / "tasks", WS / "results"
    results.mkdir(parents=True, exist_ok=True)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    done: set[str] = set()
    # `done` records ANSWERED tasks, never attempted ones: marking on attempt
    # strands a task whose sidecar was briefly down.
    attempts: dict[str, int] = {}
    retry_at: dict[str, float] = {}
    print(f"seat-ag2-assistant: worker={WORKER} acp={ACP_URL} timeout={TURN_TIMEOUT_S:.0f}s "
          f"watching {tasks}", flush=True)
    while not _STOP:
        for task in sorted(tasks.glob("task-*.txt")) if tasks.is_dir() else []:
            if not is_pending_task_file(task.name) or task.name in done:
                continue
            if time.time() < retry_at.get(task.name, 0.0):
                continue
            t0 = time.time()
            body = answer(task, results)
            if body is not None or (results / task.name).exists():
                done.add(task.name)
                attempts.pop(task.name, None)
                retry_at.pop(task.name, None)
                if body is not None:
                    print(f"seat-ag2-assistant: answered {task.stem} in {time.time() - t0:.1f}s "
                          f"({len(body)} chars)", flush=True)
            else:
                n = attempts[task.name] = attempts.get(task.name, 0) + 1
                delay = _backoff_delay(n)
                retry_at[task.name] = time.time() + delay
                print(f"seat-ag2-assistant: {task.stem} still pending after attempt {n}; "
                      f"retrying in {delay:.0f}s", file=sys.stderr, flush=True)
        time.sleep(SCAN_S)
    return 0


if __name__ == "__main__":
    sys.exit(main())
