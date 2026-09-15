#!/usr/bin/env bash
# Platform-conditional steps of src/startup.sh, extracted so each one is a
# function a test can drive with $OSTYPE pinned and fake tools on PATH.
#
# startup.sh used to inline four Darwin-only steps — caffeinate, the
# `screencapture` Screen-Recording probe, the AppleScript Accessibility probe,
# and the Homebrew fswatch auto-install. On Linux each one either aborted the
# run (`set -e` + a missing binary) or printed a macOS remedy for a macOS
# problem the host cannot have. Every function here has a Darwin branch that
# is the old behaviour verbatim and a Linux branch that checks the equivalent
# thing (an X display + a screenshot tool; xdotool for input control; the apt
# package for fswatch) and prints the remedy for THIS host.
#
# Sourced by startup.sh after scripts/platform.sh. Functions print their own
# status lines so the startup transcript reads the same on both platforms.

# Keep the display awake for an always-on host. Darwin: caffeinate (unchanged).
# Linux: disable the X screensaver/DPMS on $DISPLAY when xset is present.
# Nothing here may fail the boot — a sleeping display is a degraded host, not
# a dead one.
startup_inhibit_sleep() {
	if sutando_is_darwin; then
		if ! pgrep -q caffeinate; then
			caffeinate -d -i -s &
			echo "  ✓ caffeinate started (prevents display sleep)"
		else
			echo "  ✓ caffeinate already running"
		fi
		return 0
	fi
	if ! sutando_has_display; then
		echo "  ~ display-sleep inhibit skipped (no \$DISPLAY — headless)"
		return 0
	fi
	if command -v xset >/dev/null 2>&1 && xset s off -dpms >/dev/null 2>&1; then
		echo "  ✓ X screensaver/DPMS disabled on $DISPLAY"
	else
		echo "  ~ display-sleep inhibit skipped ($(sutando_install_hint xset))"
	fi
	return 0
}

# Sets PERM_OK=1 when a screen capture can actually be taken from this
# process, else 0 with the remedy printed. Darwin: the TCC size-discriminator
# probe, unchanged. Linux: a real display plus a screenshot tool, verified by
# taking one — the same "prove it, don't assume it" the Darwin branch does.
startup_screen_capture_check() {
	PERM_OK=1
	local probe="${TMPDIR:-/tmp}/sutando-permcheck-$$.png"
	if sutando_is_darwin; then
		# macOS 15+ silently writes a tiny PNG when Screen Recording is denied (exit 0).
		# Discriminator: real captures are hundreds-of-KB to MB; denied artifacts <2KB.
		# An all-black 5120x2880 PNG compresses to ~43KB (PNG handles flat colors well),
		# so 5KB is the safe floor — well above any denied output, well below any real
		# capture even on a locked / dark / blank desktop.
		screencapture -x "$probe" 2>/dev/null || PERM_OK=0
		if [ "$PERM_OK" -eq 1 ]; then
			# wc -c is portable across BSD (macOS) and GNU coreutils (Homebrew may override).
			local permcheck_size
			permcheck_size=$(wc -c < "$probe" 2>/dev/null | tr -d ' ' || echo 0)
			if [ "${permcheck_size:-0}" -lt 5000 ]; then PERM_OK=0; fi
		fi
		rm -f "$probe"
		if [ "$PERM_OK" -eq 0 ]; then
			echo "  ⚠ Screen Recording not granted (or stale)"
			echo "    → System Settings → Privacy & Security → Screen & System Audio Recording"
			echo "    → Add the app running this terminal (Terminal.app / iTerm2 / Warp / VS Code / Cursor / etc.)"
			echo "    → Fully Quit the terminal app, then re-open. macOS caches the perm until process restart."
			if lsof -i :7845 > /dev/null 2>&1; then
				echo "    → A screen-capture server is already running on :7845 with the old (denied) perm."
				echo "      Kill it before re-running: lsof -ti:7845 | xargs kill"
			fi
		else
			echo "  ✓ Screen Recording"
		fi
		return 0
	fi
	# Linux: no TCC. Capture works iff there is a display and a tool for it.
	if ! sutando_has_display; then
		PERM_OK=0
		echo "  ⚠ Screen capture unavailable — no \$DISPLAY (headless session)"
		echo "    → Run inside a desktop session, or start a virtual one:"
		echo "      bash scripts/linux/install-headless-desktop.sh   (Xvfb + XFCE + VNC)"
		return 0
	fi
	local tool
	if ! tool="$(sutando_linux_screenshot_tool)"; then
		PERM_OK=0
		echo "  ⚠ Screen capture unavailable — no screenshot tool on PATH"
		echo "    → $(sutando_install_hint scrot)   (or imagemagick / gnome-screenshot)"
		return 0
	fi
	case "$tool" in
		scrot)            scrot -o "$probe" >/dev/null 2>&1 || PERM_OK=0 ;;
		import)           import -window root "$probe" >/dev/null 2>&1 || PERM_OK=0 ;;
		gnome-screenshot) gnome-screenshot -f "$probe" >/dev/null 2>&1 || PERM_OK=0 ;;
	esac
	[ -s "$probe" ] || PERM_OK=0
	rm -f "$probe"
	if [ "$PERM_OK" -eq 1 ]; then
		echo "  ✓ Screen capture ($tool on $DISPLAY)"
	else
		echo "  ⚠ Screen capture failed — $tool could not grab $DISPLAY"
		echo "    → Is the X server up? Check: xdpyinfo -display $DISPLAY"
	fi
	return 0
}

# Input-control readiness. Darwin: the bounded AppleScript Accessibility probe
# (src/accessibility_probe.sh), unchanged. Linux: xdotool on PATH plus a
# display — that is what type/press/click route through here.
startup_accessibility_check() {
	local repo="$1"
	if sutando_is_darwin; then
		# $REPO is overridable, so fall back to alongside this script: an unguarded
		# source under `set -e` aborts the whole run.
		local __probe="$repo/src/accessibility_probe.sh"
		[ -f "$__probe" ] || __probe="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/accessibility_probe.sh"
		# 125 = could not check. NOT 0: a missing probe would otherwise print
		# "✓ Accessibility", asserting a grant on a run where nothing was probed.
		local acc_rc=125
		if [ -f "$__probe" ]; then
			# `|| rc=$?` keeps this exempt from `set -e`; a bare non-zero call aborts.
			# shellcheck source=accessibility_probe.sh
			source "$__probe"
			acc_rc=0; accessibility_probe || acc_rc=$?
		fi
		case $acc_rc in
			0)   echo "  ✓ Accessibility" ;;
			125) echo "  ⚠ Accessibility UNKNOWN — probe unavailable, nothing was checked" ;;
			124) echo "  ⚠ Accessibility UNKNOWN — probe timed out after ${ACCESSIBILITY_PROBE_TIMEOUT_S:-5}s"
			     echo "    This session cannot answer the prompt (headless/SSH); the grant may be fine." ;;
			*)   echo "  ⚠ Accessibility not granted"
			     echo "    → System Settings → Privacy & Security → Accessibility"
			     echo "    → Add Terminal.app or Shortcuts.app" ;;
		esac
		return 0
	fi
	if ! sutando_has_display; then
		echo "  ~ Input control skipped (no \$DISPLAY)"
	elif command -v xdotool >/dev/null 2>&1; then
		echo "  ✓ Input control (xdotool on $DISPLAY)"
	else
		echo "  ⚠ Input control unavailable — $(sutando_install_hint xdotool)"
	fi
	return 0
}

# fswatch is a hard boot requirement (the task watcher is built on it).
# Darwin: auto-install via Homebrew when present, as before. Linux: never
# sudo on the user's behalf — print the apt line and let them run it.
# Returns 0 when fswatch is usable, 1 when startup must refuse to boot.
startup_fswatch_check() {
	if command -v fswatch > /dev/null 2>&1; then
		return 0
	fi
	if sutando_is_darwin && command -v brew > /dev/null 2>&1; then
		echo "  ⚠ fswatch not found — installing via Homebrew..."
		brew install fswatch
		if command -v fswatch > /dev/null 2>&1; then
			echo "  ✓ fswatch installed"
			return 0
		fi
		echo "  ✗ fswatch installation failed"
		return 1
	fi
	echo "  ✗ fswatch not found — $(sutando_install_hint fswatch)"
	return 1
}
