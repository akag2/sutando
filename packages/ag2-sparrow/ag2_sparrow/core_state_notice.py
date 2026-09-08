"""Core-state notices: tell a room WHY the agent is silent, instead of nothing.

The transport keeps accepting and queuing tasks while the core runtime cannot
answer them — usage limit hit, logged out, crashed, wedged at an interactive
prompt. From the sender's side that was a silent drop: the message posts, the
agent never replies, and no surface says why (owner report 2026-09-07). The
detection already exists — sutando's core-input-watch writes a refined state to
``state/core-supervisor.json`` every tick — but nothing on the task path
consumed it.

This module closes that gap without touching delivery semantics:

  * The task is ALWAYS still queued (durability is the feature); the notice
    only explains the delay.
  * A room with a queued-but-unanswered task gets ONE degraded notice per
    (room, reason) per cooldown window, so a burst of messages during one
    outage produces one line, while a new failure mode re-notices.
  * When the core comes back, each noticed room gets one recovery line and its
    ledger entry clears — the next outage starts clean.
  * Notice bodies are built ONLY from fixed strings in this module. The
    supervisor file's ``prompt``/``detail`` carry core pane text; rooms span
    tiers (guest/team), so file content is never forwarded.

Fail-quiet by design: no supervisor file (standalone ag2-sparrow installs), a
malformed file, or an unrecognized state each mean "do nothing", never a
guess. There is deliberately no mtime-staleness gate — see read_core_state.
Flap-bounded: cooldown history survives recovery (see _load_ledger), so no
sequence of state changes — degraded reasons alternating, or degraded/healthy
flapping — can exceed one notice per (room, reason) plus one recovery per
delivered notice per cooldown window.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

CORE_SUPERVISOR_FILE = "core-supervisor.json"
# The gateway's ledger filename (unchanged). Every bridge sharing a workspace
# writes its OWN ledger — discord/slack/telegram pass a distinct `ledger_name`
# so concurrent writers never collide on one file and one surface's cooldown
# can't suppress another's notice. Room-id namespaces differ per surface too.
LEDGER_FILE = "core-state-notice.json"

_FIELD_MAX = 200  # supervisor fields are another process's output — bound them

# Healthy = the core can (or will imminently) pick tasks up. `blocked-known`
# is a gate the supervisor auto-answers, so it self-clears without a human.
_HEALTHY_STATES = frozenset({"idle-ready", "running", "blocked-known"})

# reason key → user-facing phrase. Keys are stable identifiers (they live in
# the on-disk ledger); phrases are the only text a room ever sees.
_REASONS = {
    "usage-limit": "my AI core has hit its usage limit and is waiting for the "
                   "limit to reset (or for my owner to intervene)",
    "logged-out": "my AI core is logged out and needs my owner to sign in again",
    "crashed": "my core process is not running",
    "hung": "my core looks stalled and may need my owner's attention",
    "blocked": "my core is stopped at a prompt that needs my owner",
}

# blocked-human refines by gate kind; unlisted kinds share the generic phrase.
_BLOCKED_KIND_TO_REASON = {
    "session-limit": "usage-limit",
    "fable-limit-unfocused": "usage-limit",
    "login": "logged-out",
}

_STATE_TO_REASON = {
    "crashed": "crashed",
    "logged-out": "logged-out",
    "hung": "hung",
}

# The gateway's original suffix; other surfaces (discord/slack/telegram) pass
# their own via the `suffix` arg so the notice names the right transport.
_NOTICE_SUFFIX = " _(automated notice from the agent's gateway)_"


def _degraded_body(reason_key: str, suffix: str = _NOTICE_SUFFIX) -> str:
    return (f"⚠️ I received your message, but I can't work on it right now: "
            f"{_REASONS[reason_key]}. It's queued and I'll pick it up as soon "
            f"as I'm back.{suffix}")


def _recovery_body(suffix: str = _NOTICE_SUFFIX) -> str:
    return ("✅ I'm back online — working through the messages that arrived "
            f"while I was unavailable.{suffix}")


def _bounded_str(v) -> str | None:
    if not isinstance(v, str):
        return None
    v = v.strip()
    return v[:_FIELD_MAX] if v else None


def read_core_state(state_dir: Path, now: float | None = None):
    """``core-supervisor.json`` → (state, kind) or None.

    None means "no verdict": file absent (standalone install), unreadable, or
    malformed. MUST NOT raise — this runs inside the poll loop and a broken
    side-channel must never become a delivery blocker (same contract as the
    heartbeat's core-status read).

    Deliberately NO mtime-staleness gate (live finding 2026-09-08): the
    watcher writes ONLY on state change (core-input-watch's `sig != last_sig`
    guard), so a stable state — including a multi-hour usage-limit outage, or
    a week-long weekly-limit one — leaves the mtime arbitrarily old while the
    content is perfectly current. An earlier 15-minute bound here therefore
    reintroduced the original silent-drop for any outage longer than the
    bound. The orphaned-file risk the bound guarded against (watcher dead,
    last state degraded) is bounded instead by the per-(room, reason)
    cooldown, and the watcher is supervisor-managed on real installs.
    """
    path = Path(state_dir) / CORE_SUPERVISOR_FILE
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        state = _bounded_str(data.get("state"))
        if state is None:
            return None
        return state, _bounded_str(data.get("kind"))
    except Exception:  # noqa: BLE001 — no verdict beats a wrong one
        return None


def degraded_reason(state: str, kind: str | None) -> str | None:
    """Supervisor state → reason key, or None when the core can serve tasks.

    Deliberately an allowlist on BOTH sides: only known-degraded states notice,
    and only known-healthy states count as recovered (see sweep). Anything
    else — ``gateway-down`` (self-referential from inside the gateway), future
    states — is "no verdict", so an unrecognized value can neither spam rooms
    nor fake a recovery.
    """
    if state == "blocked-human":
        return _BLOCKED_KIND_TO_REASON.get(kind or "", "blocked")
    return _STATE_TO_REASON.get(state)


def _cooldown_s() -> float:
    try:
        return float(os.environ.get("SPARROW_CORE_NOTICE_COOLDOWN_S") or 1800)
    except ValueError:
        return 1800.0


def _enabled() -> bool:
    return (os.environ.get("SPARROW_CORE_NOTICE") or "1").strip() != "0"


def _load_ledger(state_dir: Path, ledger_name: str = LEDGER_FILE) -> dict:
    """Two separated concerns (review 2026-09-07, should-fix #1):

    ``active``    room → reason: a degraded notice was DELIVERED and its
                  recovery line is still owed. Cleared per room by recovery.
    ``last_sent`` room → {reason → ts}: cooldown history. NEVER cleared by
                  recovery — only aged out past the cooldown window.

    The v1 schema kept one {reason, ts} per room doing both jobs, which broke
    both bounds: alternating reasons (usage-limit ↔ logged-out) overwrote each
    other's cooldown and re-sent on every alternation, and a degraded/healthy
    flap cleared the cooldown with the recovery, re-noticing immediately. A v1
    (or corrupt) file resets to empty: worst case one duplicate notice."""
    try:
        data = json.loads((Path(state_dir) / ledger_name).read_text())
        if isinstance(data, dict) and data.get("schema_version") == 2:
            active = data.get("active")
            last_sent = data.get("last_sent")
            if isinstance(active, dict) and isinstance(last_sent, dict):
                return {
                    "active": {str(r): str(v) for r, v in active.items()
                               if isinstance(v, str)},
                    "last_sent": {
                        str(r): {str(k): float(ts) for k, ts in v.items()
                                 if isinstance(ts, (int, float))}
                        for r, v in last_sent.items() if isinstance(v, dict)
                    },
                }
    except FileNotFoundError:
        pass
    except Exception:  # noqa: BLE001 — a corrupt ledger resets, never wedges
        pass
    return {"active": {}, "last_sent": {}}


def _save_ledger(state_dir: Path, ledger: dict, now: float,
                 ledger_name: str = LEDGER_FILE) -> None:
    """Atomic, best-effort. A lost ledger's worst case is one duplicate notice
    per room after a restart — strictly better than a lost notice. Cooldown
    history past its window is dead weight — prune it here so the file stays
    bounded by the set of currently-chatty rooms."""
    cooldown = _cooldown_s()
    ledger["last_sent"] = {
        room: kept for room, per_reason in ledger["last_sent"].items()
        if (kept := {k: ts for k, ts in per_reason.items()
                     if now - ts < cooldown})
    }
    path = Path(state_dir) / ledger_name
    tmp = path.with_suffix(f".json.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"schema_version": 2, **ledger}))
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)


class NoticePlan:
    """What a sweep WOULD send this pass, decided but not yet sent — so a caller
    that sends synchronously (gateway) and one that sends with ``await``
    (discord/slack/telegram) can share the exact ledger + cooldown logic.

    ``kind`` ∈ {"degraded", "recovery"}; ``items`` is a list of (room, body)
    to attempt. The caller sends each, collects the rooms that SUCCEEDED, and
    passes them back to :meth:`commit` — so a failed send burns nothing and the
    next pass retries. Nothing is persisted until commit.
    """

    __slots__ = ("kind", "reason", "items", "_ledger", "_now",
                 "_state_dir", "_ledger_name")

    def __init__(self, kind, reason, items, ledger, now, state_dir, ledger_name):
        self.kind, self.reason, self.items = kind, reason, items
        self._ledger, self._now = ledger, now
        self._state_dir, self._ledger_name = state_dir, ledger_name

    def commit(self, sent_rooms) -> None:
        sent = set(sent_rooms)
        if not sent and self.kind == "degraded":
            return  # nothing delivered → nothing to persist (recovery still prunes)
        if self.kind == "degraded":
            for room in sent:
                self._ledger["last_sent"].setdefault(room, {})[self.reason] = self._now
                self._ledger["active"][room] = self.reason
        else:  # recovery: the owed notice is discharged only once delivered
            for room in sent:
                self._ledger["active"].pop(room, None)
        _save_ledger(self._state_dir, self._ledger, self._now, self._ledger_name)


def plan_notices(state_dir, rooms, now=None,
                 ledger_name: str = LEDGER_FILE) -> "NoticePlan | None":
    """Read core state + ledger and decide what to send — WITHOUT sending.

    Returns None when there is nothing to do (feature disabled, no verdict, an
    unrecognized state, cooldown covers every room, or no recovery owed). The
    same spam bound holds regardless of caller: per room, ≤ one degraded notice
    per reason per cooldown window, and ≤ one recovery per DELIVERED notice.
    """
    if not _enabled():
        return None
    verdict = read_core_state(state_dir, now)
    if verdict is None:
        return None  # no evidence either way — neither notice nor recovery
    now = now if now is not None else time.time()
    state, kind = verdict
    reason = degraded_reason(state, kind)
    ledger = _load_ledger(state_dir, ledger_name)
    if reason is not None:
        cooldown = _cooldown_s()
        items = [(room, _degraded_body(reason))
                 for room in sorted(set(rooms))
                 if not (0 <= now - (ledger["last_sent"].get(room, {})
                                     .get(reason, -cooldown - 1)) < cooldown)]
        return NoticePlan("degraded", reason, items, ledger, now,
                          state_dir, ledger_name)
    if state not in _HEALTHY_STATES:
        return None  # unrecognized state — not proof of recovery
    if not ledger["active"]:
        return None
    items = [(room, _recovery_body()) for room in sorted(ledger["active"])]
    return NoticePlan("recovery", None, items, ledger, now,
                      state_dir, ledger_name)


def sweep_core_state_notices(state_dir: Path, rooms, send, log=None,
                             now: float | None = None) -> None:
    """One synchronous pass: notice degraded, announce recovery, persist.

    Thin wrapper over :func:`plan_notices` for a caller with a synchronous
    ``send(room, body) → bool`` (the gateway bridge). ``send`` returning False
    means not delivered, so no ledger entry is written and the next pass
    retries; exceptions from ``send`` propagate (the caller owns auth/transport
    policy). Async callers use ``plan_notices`` + ``NoticePlan.commit`` instead.
    """
    plan = plan_notices(state_dir, rooms, now)
    if plan is None:
        return
    verb = "notice" if plan.kind == "degraded" else "recovery notice"
    sent = []
    for room, body in plan.items:
        if send(room, body):
            sent.append(room)
            if log:
                extra = f" ({plan.reason})" if plan.kind == "degraded" else ""
                log(f"core-state {verb}{extra} sent to {room}")
    plan.commit(sent)
