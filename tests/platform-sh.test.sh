#!/usr/bin/env bash
# Contract test for scripts/platform.sh — the shell's single answer to "which
# OS is this" and "how do I install X here".
#
# Platform identity must come from $OSTYPE, never from a PATH lookup: a stubbed
# `uname` on the Linux CI runner once flipped python-binary.sh into its macOS
# branch (see that file). Every case below PINS OSTYPE so it runs identically
# on a Mac and on ubuntu-latest.
#
# Run: bash tests/platform-sh.test.sh
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
pass=0; fail=0
ok()   { printf "  ok   %s\n" "$1"; pass=$((pass+1)); }
bad()  { printf "  FAIL %s — %s\n" "$1" "${2:-}"; fail=$((fail+1)); }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "expected [$3], got [$2]"; fi; }


# Isolated command inventory: the fakes, plus symlinks to the handful of
# coreutils the helpers legitimately use. Nothing else from the host — a Linux
# runner with ImageMagick/fswatch/lsof installed must not flip an "absent" case.
mkcore() {
  local d="$1"; mkdir -p "$d/core"
  local t
  for t in wc tr rm dirname head cat grep sed seq sleep pgrep; do
    local src; src="$(command -v "$t" 2>/dev/null)" || continue
    ln -sf "$src" "$d/core/$t"
  done
  printf '%s' "$d/core"
}
CORE="$(mkcore "$(mktemp -d)")"

run() { # $1 = OSTYPE, $2 = extra PATH dir (may be empty), $3 = snippet
  local extra="${2:-}"
  OSTYPE="$1" PATH="${extra:+$extra:}$CORE" DISPLAY="${DISPLAY_OVERRIDE-}" WAYLAND_DISPLAY="" \
    /bin/bash -c ". '$REPO/scripts/platform.sh'; $3"
}

echo "platform.sh:"

# --- identity ----------------------------------------------------------------
check "darwin from OSTYPE"  "$(run darwin25 '' 'sutando_os')" "darwin"
check "linux from OSTYPE"   "$(run linux-gnu '' 'sutando_os')" "linux"
check "other from OSTYPE"   "$(run freebsd14 '' 'sutando_os')" "other"
# A stubbed uname must not win over $OSTYPE.
lab=$(mktemp -d); printf '#!/bin/sh\necho Darwin\n' > "$lab/uname"; chmod +x "$lab/uname"
check "OSTYPE outranks a PATH uname stub" "$(run linux-gnu "$lab" 'sutando_os')" "linux"
check "is_linux predicate" "$(run linux-gnu '' 'sutando_is_linux && echo yes || echo no')" "yes"
check "is_darwin predicate" "$(run linux-gnu '' 'sutando_is_darwin && echo yes || echo no')" "no"

# --- install hints -----------------------------------------------------------
check "darwin hint is brew" "$(run darwin25 '' 'sutando_install_hint fswatch')" "brew install fswatch"
# apt present → apt hint, with the package name mapped where it differs.
aptlab=$(mktemp -d); printf '#!/bin/sh\nexit 0\n' > "$aptlab/apt-get"; chmod +x "$aptlab/apt-get"
check "linux+apt hint"        "$(run linux-gnu "$aptlab" 'sutando_install_hint fswatch')" "sudo apt-get install -y fswatch"
check "linux+apt maps xset"   "$(run linux-gnu "$aptlab" 'sutando_install_hint xset')" "sudo apt-get install -y x11-xserver-utils"
check "linux+apt maps import" "$(run linux-gnu "$aptlab" 'sutando_install_hint import')" "sudo apt-get install -y imagemagick"
case "$(run linux-gnu "$aptlab" 'sutando_install_hint node')" in
  *NodeSource*|*nodesource*) ok "linux node hint points at NodeSource, not apt nodejs" ;;
  *) bad "linux node hint points at NodeSource, not apt nodejs" "$(run linux-gnu "$aptlab" 'sutando_install_hint node')" ;;
esac
# No package manager at all → still a sentence, never empty.
nopm=$(mktemp -d)
out=$(OSTYPE=linux-gnu PATH="$nopm:$CORE" /bin/bash -c ". '$REPO/scripts/platform.sh'; sutando_install_hint fswatch")
check "linux without a package manager still hints" "$out" "install fswatch with your package manager"

# --- display -----------------------------------------------------------------
check "darwin always has a display" "$(run darwin25 '' 'sutando_has_display && echo yes || echo no')" "yes"
check "linux without DISPLAY has none" "$(run linux-gnu '' 'sutando_has_display && echo yes || echo no')" "no"
check "linux with DISPLAY has one" "$(DISPLAY_OVERRIDE=:99 run linux-gnu '' 'sutando_has_display && echo yes || echo no')" "yes"

# --- screenshot tool + launchd -----------------------------------------------
shot=$(mktemp -d); printf '#!/bin/sh\nexit 0\n' > "$shot/import"; chmod +x "$shot/import"
check "screenshot tool: import when scrot absent" "$(run linux-gnu "$shot" 'sutando_linux_screenshot_tool')" "import"
printf '#!/bin/sh\nexit 0\n' > "$shot/scrot"; chmod +x "$shot/scrot"
check "screenshot tool: scrot preferred" "$(run linux-gnu "$shot" 'sutando_linux_screenshot_tool')" "scrot"
check "screenshot tool: none → rc 1" "$(run linux-gnu '' 'sutando_linux_screenshot_tool >/dev/null; echo $?')" "1"
lc=$(mktemp -d); printf '#!/bin/sh\nexit 0\n' > "$lc/launchctl"; chmod +x "$lc/launchctl"
check "launchd never on linux even with a launchctl on PATH" "$(run linux-gnu "$lc" 'sutando_has_launchd && echo yes || echo no')" "no"
check "launchd on darwin with launchctl" "$(run darwin25 "$lc" 'sutando_has_launchd && echo yes || echo no')" "yes"

rm -rf "$lab" "$aptlab" "$nopm" "$shot" "$lc" "$(dirname "$CORE")"
echo "Summary: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
