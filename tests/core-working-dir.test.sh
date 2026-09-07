#!/usr/bin/env bash
# scripts/core-working-dir.sh — the one resolver for SUTANDO_CLAUDE_WORKING_DIR.
#
# Two halves. (1) The contract on the four input shapes. (2) Delegation: every site that
# decides or targets the core's launch dir sources the resolver and none keeps a private
# `${v/#\~/$HOME}` expansion — four private copies disagreed on `~user` (the launcher created
# and launched into $HOME + "user/…" while the installer refused it), which is the defect.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
RESOLVER="$REPO/scripts/core-working-dir.sh"
unset SUTANDO_CLAUDE_WORKING_DIR

pass=0; fail=0
ok() { if [ "$2" = 0 ]; then echo "ok   $1"; pass=$((pass+1)); else echo "FAIL $1"; fail=$((fail+1)); fi; }

ROOT="$(mktemp -d "${TMPDIR:-/tmp}/core cwd test.XXXXXX")"
export HOME="$ROOT/home"; mkdir -p "$HOME"
. "$RESOLVER"

# --- 1. contract ---------------------------------------------------------------
ok "unset: prints the default unchanged" "$([ "$(sutando_core_working_dir "$ROOT/default")" = "$ROOT/default" ] && echo 0 || echo 1)"
ok "empty: prints the default unchanged" "$([ "$(SUTANDO_CLAUDE_WORKING_DIR= sutando_core_working_dir "$ROOT/default")" = "$ROOT/default" ] && echo 0 || echo 1)"
OUT="$(SUTANDO_CLAUDE_WORKING_DIR="$ROOT/abs dir" sutando_core_working_dir "$ROOT/default")"; RC=$?
ok "absolute: created and printed physically" "$([ $RC = 0 ] && [ -d "$ROOT/abs dir" ] && [ "$OUT" = "$(cd "$ROOT/abs dir" && pwd -P)" ] && echo 0 || echo 1)"
OUT="$(SUTANDO_CLAUDE_WORKING_DIR="~/core home" sutando_core_working_dir "$ROOT/default")"; RC=$?
ok "~/: resolves under HOME" "$([ $RC = 0 ] && [ "$OUT" = "$(cd "$HOME/core home" && pwd -P)" ] && echo 0 || echo 1)"
ERR="$(SUTANDO_CLAUDE_WORKING_DIR="~someoneelse/core" sutando_core_working_dir "$ROOT/default" 2>&1 >/dev/null)"; RC=$?
ok "~user: refused (rc 1)" "$([ $RC = 1 ] && echo 0 || echo 1)"
ok "~user: message names the contract" "$(echo "$ERR" | grep -q "absolute path or start with ~/" && echo 0 || echo 1)"
ok "~user: nothing created (no HOME+user mangling)" "$([ ! -e "$HOME/someoneelse" ] && [ ! -e "${HOME}someoneelse" ] && echo 0 || echo 1)"
SUTANDO_CLAUDE_WORKING_DIR="relative/dir" sutando_core_working_dir "$ROOT/default" >/dev/null 2>&1; RC=$?
ok "relative: refused (rc 1)" "$([ $RC = 1 ] && echo 0 || echo 1)"
ok "relative: nothing created" "$([ ! -e "$PWD/relative" ] && echo 0 || echo 1)"

# --- 2. delegation: every deciding site sources the resolver, none keeps a private copy -------
SITES=(src/agent/claude/cli/start-cli.sh src/install-claude-hooks.sh scripts/install-personal-claude-hook.sh scripts/install-session-start-hook.sh)
for s in "${SITES[@]}"; do
  ok "$s sources scripts/core-working-dir.sh" "$(grep -q 'scripts/core-working-dir.sh' "$REPO/$s" && echo 0 || echo 1)"
  ok "$s calls sutando_core_working_dir" "$(grep -q 'sutando_core_working_dir' "$REPO/$s" && echo 0 || echo 1)"
  ok "$s keeps no private tilde expansion" "$(grep -q '/#\\~/' "$REPO/$s" && echo 1 || echo 0)"
done
# The probe cannot source bash; it mirrors the contract and must say so by name.
ok "health-check names the shared resolver as the contract it mirrors" "$(grep -q 'core-working-dir.sh' "$REPO/src/health-check.py" && echo 0 || echo 1)"

rm -rf "$ROOT"
echo "---"
if [ "$fail" -gt 0 ]; then echo "FAILED — $fail of $((pass+fail)) checks"; exit 1; fi
echo "PASS — core-working-dir ($pass checks)"
