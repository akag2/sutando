#!/usr/bin/env bash
# Contract test for src/startup-platform.sh — the four boot steps startup.sh
# used to inline as Darwin-only (sleep inhibit, screen-capture probe,
# accessibility probe, fswatch check), now functions with a Linux branch.
#
# Exercised with OSTYPE pinned and every external tool faked on PATH, so the
# assertions hold on a Mac and on ubuntu-latest alike. The failure modes that
# motivated the change are each reproduced: a Linux host with no $DISPLAY must
# report capture OFF (not crash on `screencapture`), a missing fswatch must
# print the apt line (not `brew install`), and a Darwin host must keep its
# exact old verdict path.
#
# Run: bash tests/startup-platform.test.sh
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
pass=0; fail=0
ok()   { printf "  ok   %s\n" "$1"; pass=$((pass+1)); }
bad()  { printf "  FAIL %s — %s\n" "$1" "${2:-}"; fail=$((fail+1)); }
has()  { if printf '%s' "$2" | grep -qF -- "$3"; then ok "$1"; else bad "$1" "missing [$3] in: $2"; fi; }
lacks(){ if printf '%s' "$2" | grep -qF -- "$3"; then bad "$1" "unexpected [$3] in: $2"; else ok "$1"; fi; }

mkfake() { printf '#!/bin/sh\n%s\n' "$2" > "$1"; chmod +x "$1"; }

# $1 OSTYPE, $2 fake bin dir, $3 DISPLAY, $4 snippet → stdout
run() {
  OSTYPE="$1" PATH="$2:/usr/bin:/bin" DISPLAY="$3" WAYLAND_DISPLAY="" TMPDIR="$2" \
    /bin/bash -c ". '$REPO/scripts/platform.sh'; . '$REPO/src/startup-platform.sh'; $4" 2>&1
}

echo "startup-platform.sh:"

# --- screen capture: linux -----------------------------------------------------
lab=$(mktemp -d)
out=$(run linux-gnu "$lab" "" 'startup_screen_capture_check; echo PERM_OK=$PERM_OK')
has   "linux, no DISPLAY → capture off"            "$out" "PERM_OK=0"
has   "linux, no DISPLAY → names the cause"        "$out" 'no $DISPLAY'
lacks "linux never mentions System Settings"       "$out" "System Settings"

out=$(run linux-gnu "$lab" ":99" 'startup_screen_capture_check; echo PERM_OK=$PERM_OK')
has   "linux, DISPLAY but no tool → capture off"   "$out" "PERM_OK=0"
has   "linux, no tool → apt hint for scrot"        "$out" "scrot"

# scrot that writes a real (non-empty) file → on.
mkfake "$lab/scrot" 'echo x > "$2"'
out=$(run linux-gnu "$lab" ":99" 'startup_screen_capture_check; echo PERM_OK=$PERM_OK')
has   "linux, scrot succeeds → capture on"         "$out" "PERM_OK=1"
has   "linux, reports tool + display"              "$out" "scrot on :99"

# scrot that exits 0 but writes nothing → off (the size check, not the rc).
mkfake "$lab/scrot" 'exit 0'
out=$(run linux-gnu "$lab" ":99" 'startup_screen_capture_check; echo PERM_OK=$PERM_OK')
has   "linux, scrot writes no file → capture off"  "$out" "PERM_OK=0"

# --- screen capture: darwin (old verdict path, verbatim) -----------------------
dlab=$(mktemp -d)
mkfake "$dlab/screencapture" 'head -c 100 /dev/zero > "$2"'   # tiny = TCC-denied artifact
mkfake "$dlab/lsof" 'exit 1'
out=$(run darwin25 "$dlab" "" 'startup_screen_capture_check; echo PERM_OK=$PERM_OK')
has   "darwin, tiny PNG → denied"                  "$out" "PERM_OK=0"
has   "darwin, denied → System Settings remedy"    "$out" "System Settings"
mkfake "$dlab/screencapture" 'head -c 9000 /dev/zero > "$2"'
out=$(run darwin25 "$dlab" "" 'startup_screen_capture_check; echo PERM_OK=$PERM_OK')
has   "darwin, real-size PNG → granted"            "$out" "PERM_OK=1"

# --- accessibility / input control ---------------------------------------------
out=$(run linux-gnu "$lab" ":99" "startup_accessibility_check '$REPO'")
has   "linux, no xdotool → apt hint"               "$out" "xdotool"
lacks "linux never claims Accessibility granted"   "$out" "✓ Accessibility"
mkfake "$lab/xdotool" 'exit 0'
out=$(run linux-gnu "$lab" ":99" "startup_accessibility_check '$REPO'")
has   "linux, xdotool present → input control on"  "$out" "✓ Input control (xdotool on :99)"
out=$(run linux-gnu "$lab" "" "startup_accessibility_check '$REPO'")
has   "linux, no DISPLAY → skipped, not failed"    "$out" "skipped"

# --- sleep inhibit -------------------------------------------------------------
out=$(run linux-gnu "$lab" "" 'startup_inhibit_sleep; echo rc=$?')
has   "linux headless: inhibit skipped, rc 0"      "$out" "rc=0"
lacks "linux never runs caffeinate"                "$out" "caffeinate"
mkfake "$lab/xset" 'echo "xset $*" >> "'"$lab"'/xset.log"'
out=$(run linux-gnu "$lab" ":99" 'startup_inhibit_sleep; echo rc=$?')
has   "linux + DISPLAY + xset → DPMS off"          "$out" "DPMS disabled"
has   "xset invoked with s off -dpms"              "$(cat "$lab/xset.log")" "s off -dpms"

# --- fswatch -------------------------------------------------------------------
nof=$(mktemp -d)
mkfake "$nof/apt-get" 'exit 0'
out=$(run linux-gnu "$nof" "" 'startup_fswatch_check; echo rc=$?')
has   "linux, no fswatch → boot refuses (rc 1)"    "$out" "rc=1"
has   "linux, no fswatch → apt line"               "$out" "sudo apt-get install -y fswatch"
lacks "linux never suggests brew"                  "$out" "brew"
mkfake "$nof/fswatch" 'exit 0'
out=$(run linux-gnu "$nof" "" 'startup_fswatch_check; echo rc=$?')
has   "fswatch present → rc 0"                     "$out" "rc=0"
# darwin without brew: the old message, verbatim shape.
dno=$(mktemp -d)
out=$(run darwin25 "$dno" "" 'startup_fswatch_check; echo rc=$?')
has   "darwin, no fswatch, no brew → rc 1"         "$out" "rc=1"
has   "darwin hint is brew"                        "$out" "brew install fswatch"

rm -rf "$lab" "$dlab" "$nof" "$dno"
echo "Summary: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
