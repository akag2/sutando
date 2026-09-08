#!/bin/bash
# UserPromptSubmit hook: a new turn is starting, so re-arm the Stop reminder.
# Fails open and silent — it runs before every prompt and must never block one.
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# A bare `python3` fallback re-enters the very CLT stub the resolver refused,
# so a refusal means skip the reset rather than run an unvalidated interpreter.
if ! PYBIN="$(bash "$REPO_DIR/scripts/sutando-config.sh" python-bin 2>/dev/null)" \
   || [ -z "$PYBIN" ] || [ ! -x "$PYBIN" ]; then
  exit 0
fi
"$PYBIN" "$REPO_DIR/src/turn_ledger.py" turn-start >/dev/null 2>&1
exit 0
