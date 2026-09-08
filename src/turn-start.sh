#!/bin/bash
# UserPromptSubmit hook: a new turn is starting, so re-arm the Stop reminder.
# Fails open and silent — this runs before every prompt and must never be able
# to block one.
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYBIN="$(bash "$REPO_DIR/scripts/sutando-config.sh" python-bin 2>/dev/null)"
[ -n "$PYBIN" ] && [ -x "$PYBIN" ] || PYBIN="python3"
"$PYBIN" "$REPO_DIR/src/turn_ledger.py" turn-start >/dev/null 2>&1
exit 0
