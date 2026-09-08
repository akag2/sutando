#!/bin/bash
# Stop hook: blocks Claude from finishing when unprocessed tasks exist.
# Skips tasks that already have a corresponding result file.
#
# The queue lives under the WORKSPACE, not the repo (CLAUDE.md "Workspace
# contract"). This hook used to resolve `$(dirname "$0")/..` — the repo root —
# so it watched <repo>/tasks/ while every producer and consumer used
# <workspace>/tasks/. That directory is empty on a normal install, so the hook
# emitted `{}` on every Stop and never blocked on anything: a guard that cannot
# fire is a guard that is switched off.
#
# Resolve through the same helper every other service uses, so a configured
# workspace (sutando.config.local.json) is honored rather than assumed.

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WORKSPACE="$(bash "$REPO_DIR/scripts/sutando-config.sh" workspace 2>/dev/null)"
# Fall back to the documented default, never to the repo root: a resolver
# failure must still leave this pointed at a real queue rather than silently
# re-disabling the hook the way the old path did.
[ -n "$WORKSPACE" ] || WORKSPACE="$REPO_DIR/workspace"

# Resolve the interpreter through the repo's contract: a bare `python3` is absent
# or wrong on installs using a configured or bundled Python, and the hook would
# then emit nothing for a nonempty queue — a guard that silently cannot fire.
PYBIN="$(bash "$REPO_DIR/scripts/sutando-config.sh" python-bin 2>/dev/null)"
[ -n "$PYBIN" ] && [ -x "$PYBIN" ] || PYBIN="python3"

TASKS_DIR="$WORKSPACE/tasks"
RESULTS_DIR="$WORKSPACE/results"

UNPROCESSED=""
shopt -s nullglob 2>/dev/null
for f in "$TASKS_DIR"/*.txt; do
  BASENAME=$(basename "$f")
  # Skip if result already exists
  [ -f "$RESULTS_DIR/$BASENAME" ] && continue
  UNPROCESSED+="--- $BASENAME ---
$(cat "$f")

"
done

if [ -n "$UNPROCESSED" ]; then
  # Encode with a real JSON encoder. Hand-rolled escaping emitted this format
  # string's own \n as a raw newline inside a JSON string value, which is
  # illegal, so every block decision was unparseable and the guard never fired.
  SUTANDO_HOOK_BODY="$UNPROCESSED" "$PYBIN" -c 'import json,os,sys; sys.stdout.write(json.dumps({"decision":"block","reason":"Unprocessed tasks in tasks/","additionalContext":"UNPROCESSED TASKS — process these NOW:\n"+os.environ.get("SUTANDO_HOOK_BODY","")}, separators=(",",":"), ensure_ascii=False))'
else
  echo '{}'
fi
