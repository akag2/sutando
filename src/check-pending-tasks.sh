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

# The resolver owns which interpreter is usable; a bare `python3` is wrong on a
# configured install and re-enters the CLT stub it refused, so a refusal stands.
if ! PYBIN="$(bash "$REPO_DIR/scripts/sutando-config.sh" python-bin 2>/dev/null)" \
   || [ -z "$PYBIN" ] || [ ! -x "$PYBIN" ]; then
  PYBIN=""
fi

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

if [ -n "$UNPROCESSED" ] && [ -z "$PYBIN" ]; then
  # Encoding needs an interpreter the resolver would not supply. Say so on stderr
  # and allow the stop: a hand-rolled JSON block is what made this guard unparseable.
  echo "check-pending-tasks: no usable interpreter; queue not reported" >&2
  echo '{}'
elif [ -n "$UNPROCESSED" ]; then
  # A real JSON encoder: hand-rolled escaping put a raw newline inside a string
  # value, so every block decision was unparseable and the guard never fired.
  SUTANDO_HOOK_BODY="$UNPROCESSED" "$PYBIN" -c 'import json,os,sys; sys.stdout.write(json.dumps({"decision":"block","reason":"Unprocessed tasks in tasks/","additionalContext":"UNPROCESSED TASKS — process these NOW:\n"+os.environ.get("SUTANDO_HOOK_BODY","")}, separators=(",",":"), ensure_ascii=False))'
else
  echo '{}'
fi
