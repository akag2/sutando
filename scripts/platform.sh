#!/usr/bin/env bash
# Host-platform helpers shared by the shell boot path (startup.sh, init.sh,
# verify-setup.sh, start.sh). Sourced, never executed.
#
# Sutando was macOS-only; every "brew install X" hint, `caffeinate`,
# `screencapture`, `osascript` and `launchctl` call assumed Darwin. This file is
# the ONE place that answers "which OS is this" and "how do I install X here",
# so a Linux host (Debian/Ubuntu first) gets accurate hints and the boot path
# can degrade per-feature instead of failing on the first Darwin-only binary.
#
# Platform identity comes from $OSTYPE first (set by the shell at build time, so
# a PATH stub cannot spoof it — see scripts/python-binary.sh for the CI incident
# that rule comes from) and only then from `uname -s`.
#
# Usage:
#   . "$REPO/scripts/platform.sh"
#   if sutando_is_linux; then ...; fi
#   echo "Fix: $(sutando_install_hint fswatch)"

sutando_os() {
	case "${OSTYPE:-$(uname -s 2>/dev/null)}" in
		darwin*|Darwin) printf 'darwin' ;;
		linux*|Linux)   printf 'linux' ;;
		*)              printf 'other' ;;
	esac
}

sutando_is_darwin() { [ "$(sutando_os)" = darwin ]; }
sutando_is_linux()  { [ "$(sutando_os)" = linux ]; }

# The Linux package manager on PATH, or empty. apt first: Debian/Ubuntu is the
# supported Linux target; the others only shape the hint text.
sutando_linux_pkg_manager() {
	for _pm in apt-get dnf yum pacman zypper apk; do
		if command -v "$_pm" >/dev/null 2>&1; then printf '%s' "$_pm"; return 0; fi
	done
	return 1
}

# Map a Sutando dependency name to the package that provides it on this host.
# Only names whose package differs from the tool name need an entry.
_sutando_pkg_name() {
	case "$(sutando_os):$1" in
		linux:node)      printf 'nodejs' ;;
		linux:npx)       printf 'nodejs' ;;
		linux:pip3)      printf 'python3-pip' ;;
		linux:xdotool)   printf 'xdotool' ;;
		linux:import)    printf 'imagemagick' ;;
		linux:convert)   printf 'imagemagick' ;;
		linux:xclip)     printf 'xclip' ;;
		linux:notify-send) printf 'libnotify-bin' ;;
		linux:xset)      printf 'x11-xserver-utils' ;;
		linux:xdpyinfo)  printf 'x11-utils' ;;
		*)               printf '%s' "$1" ;;
	esac
}

# One-line install command for dependency $1 on THIS host. Node on Linux is the
# exception: distro nodejs packages lag the >=22 floor, so point at NodeSource.
sutando_install_hint() {
	_tool="$1"
	case "$(sutando_os)" in
		darwin)
			printf 'brew install %s' "$(_sutando_pkg_name "$_tool")"
			;;
		linux)
			if [ "$_tool" = node ] || [ "$_tool" = npx ]; then
				printf 'install Node.js 22+ (https://github.com/nodesource/distributions — apt nodejs is too old)'
				return 0
			fi
			_pkg="$(_sutando_pkg_name "$_tool")"
			case "$(sutando_linux_pkg_manager || true)" in
				apt-get) printf 'sudo apt-get install -y %s' "$_pkg" ;;
				dnf)     printf 'sudo dnf install -y %s' "$_pkg" ;;
				yum)     printf 'sudo yum install -y %s' "$_pkg" ;;
				pacman)  printf 'sudo pacman -S %s' "$_pkg" ;;
				zypper)  printf 'sudo zypper install %s' "$_pkg" ;;
				apk)     printf 'sudo apk add %s' "$_pkg" ;;
				*)       printf 'install %s with your package manager' "$_pkg" ;;
			esac
			;;
		*)
			printf 'install %s' "$_tool"
			;;
	esac
}

# True when a graphical session is reachable from this process. On macOS the
# window server is always there for a logged-in user; on Linux it is $DISPLAY
# (X11, including an Xvfb virtual desktop) or $WAYLAND_DISPLAY.
sutando_has_display() {
	case "$(sutando_os)" in
		darwin) return 0 ;;
		linux)  [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ] ;;
		*)      return 1 ;;
	esac
}

# The Linux screenshot tool the capture server will use, in its own preference
# order (scrot, ImageMagick import, gnome-screenshot), or empty + rc 1.
sutando_linux_screenshot_tool() {
	for _t in scrot import gnome-screenshot; do
		if command -v "$_t" >/dev/null 2>&1; then printf '%s' "$_t"; return 0; fi
	done
	return 1
}

# The launchd/systemd question, answered once: only Darwin has the launchd
# supervision the boot path knows how to drive. Linux runs services directly.
sutando_has_launchd() {
	sutando_is_darwin && command -v launchctl >/dev/null 2>&1
}
