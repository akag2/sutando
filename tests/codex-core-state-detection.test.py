#!/usr/bin/env python3
"""Codex-core detection for the silent-core notices.

A Codex core's logged-out state can't be scraped from Claude's auth prompt.
Instead runtime-health asks `codex login status` (exit 0 = signed in), and
core-input-watch skips the Claude-only pane gate classifier for a codex core so
that neutral verdict (offline/needs_login/working/idle) drives the supervisor
state. Crash/hang/working/idle are already runtime-neutral. Codex rate-limit is
a tracked follow-up (no clean signal yet).

    python3 tests/codex-core-state-detection.test.py
"""
import importlib.util
import os
import sys
import types
from unittest.mock import patch

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rh = _load("runtime_health_codex", "src/runtime-health.py")
ciw = _load("core_input_watch_codex", "src/core-input-watch.py")

fails = 0


def check(name, cond):
    global fails
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        fails += 1


class _Proc:
    def __init__(self, rc):
        self.returncode = rc
        self.stdout = ""


def _reset_caches():
    rh._CORE_ENV_CACHE.clear()
    rh._CODEX_LOGIN_CACHE[0] = 0.0
    rh._CODEX_LOGIN_CACHE[1] = None


# 1) core_runtime() reads SUTANDO_CORE_RUNTIME; defaults to claude on failure.
_reset_caches()
with patch.object(rh, "_core_session_env", lambda v: "codex" if v == "SUTANDO_CORE_RUNTIME" else None):
    check("core_runtime: reads codex from the session env", rh.core_runtime() == "codex")
_reset_caches()
with patch.object(rh, "_core_session_env", lambda v: None), \
        patch.dict(os.environ, {}, clear=False):
    os.environ.pop("SUTANDO_CORE_RUNTIME", None)
    check("core_runtime: defaults to claude when unknown", rh.core_runtime() == "claude")

# 2) A logged-out codex core → _login_signal() True (via `codex login status` != 0).
_reset_caches()
with patch.object(rh, "core_runtime", lambda: "codex"), \
        patch.object(rh, "_core_session_env", lambda v: None), \
        patch.object(rh.subprocess, "run", lambda *a, **k: _Proc(1)):
    check("codex logged-out: _login_signal True", rh._login_signal() is True)

# 3) A signed-in codex core → False (exit 0), and never scrapes the pane.
_reset_caches()
with patch.object(rh, "core_runtime", lambda: "codex"), \
        patch.object(rh, "_core_session_env", lambda v: None), \
        patch.object(rh, "_pane_text", lambda: (_ for _ in ()).throw(AssertionError("pane read for codex"))), \
        patch.object(rh.subprocess, "run", lambda *a, **k: _Proc(0)):
    check("codex signed-in: _login_signal False (no pane scrape)", rh._login_signal() is False)

# 4) `codex login status` can't run → unknown → False (never a false logged-out).
_reset_caches()
with patch.object(rh, "core_runtime", lambda: "codex"), \
        patch.object(rh, "_core_session_env", lambda v: None), \
        patch.object(rh.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no codex"))):
    check("codex probe fails: _login_signal False (no false logged-out)", rh._login_signal() is False)

# 5) Claude path is unchanged: _login_signal defers to needs_login(pane).
_reset_caches()
with patch.object(rh, "core_runtime", lambda: "claude"), \
        patch.object(rh, "_pane_text", lambda: "Please run /login"):
    check("claude path unchanged: pane-scraped login still works", rh._login_signal() is True)

# 6) compose_state: for a codex core, needs_login → logged-out even if the pane
#    would trip a Claude gate signature (classifier is skipped for codex).
claude_login_menu = "Select login method\n❯ 1. Subscription\nPaste code here"
st_codex = ciw.compose_state(claude_login_menu, "needs_login", True, runtime="codex")
check("codex compose_state: needs_login → logged-out (claude classifier skipped)",
      st_codex[0] == "logged-out")
# Same input on the claude runtime still classifies the gate (unchanged).
st_claude = ciw.compose_state(claude_login_menu, "needs_login", True, runtime="claude")
check("claude compose_state: still classifies the login gate",
      st_claude[0] in ("blocked-human", "logged-out"))

# 7) codex crash is runtime-neutral (offline → crashed regardless of runtime).
check("codex crash → crashed (neutral)",
      ciw.compose_state("", "offline", True, runtime="codex")[0] == "crashed")

if fails:
    print(f"\n{fails} FAILURE(S)")
    sys.exit(1)
print("PASS: codex core-state detection (logout via codex login status; neutral crash/hang)")
