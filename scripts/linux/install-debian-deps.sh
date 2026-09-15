#!/usr/bin/env bash
# Install every runtime dependency Sutando needs on a Debian/Ubuntu host.
#
# Mirrors the "Required to boot" + "Required per feature" tables in
# docs/runtime-dependencies.md, translated to apt package names, plus the X11
# desktop tools the Linux inline-tool backend uses (xdotool, xclip, scrot,
# ImageMagick, notify-send, pactl). Node.js comes from NodeSource because the
# distro package is below the >=22 floor in package.json. The Claude Code CLI
# is installed for the invoking user with Anthropic's native installer.
#
# Idempotent: apt and the installers skip what is already present. Needs sudo
# for apt; never run as root directly (the CLI must land in YOUR home).
#
# Usage:
#   bash scripts/linux/install-debian-deps.sh            # everything
#   bash scripts/linux/install-debian-deps.sh --no-cli   # skip the Claude Code install
set -euo pipefail

if [ "$(id -u)" = 0 ]; then
	echo "install-debian-deps: run as your normal user (it uses sudo for apt) — the agent CLI must install into your home, not root's" >&2
	exit 2
fi
if ! command -v apt-get >/dev/null 2>&1; then
	echo "install-debian-deps: apt-get not found — this installer targets Debian/Ubuntu" >&2
	exit 2
fi

WITH_CLI=1
for arg in "$@"; do
	case "$arg" in
		--no-cli) WITH_CLI=0 ;;
		-h|--help) sed -n '2,18p' "$0"; exit 0 ;;
		*) echo "install-debian-deps: unknown arg $arg" >&2; exit 2 ;;
	esac
done

export DEBIAN_FRONTEND=noninteractive
echo "→ apt packages"
sudo apt-get update -y
sudo apt-get install -y \
	curl ca-certificates gnupg git build-essential \
	python3 python3-pip python3-venv \
	lsof procps psmisc tmux fswatch ffmpeg jq perl \
	xdotool xclip xsel scrot imagemagick x11-utils x11-xserver-utils libnotify-bin \
	pulseaudio-utils

# Node.js >= 22 (NodeSource). Skipped when an adequate node is already on PATH.
need_node=1
if command -v node >/dev/null 2>&1; then
	major="$(node --version | sed 's/^v//' | cut -d. -f1)"
	[ "${major:-0}" -ge 22 ] && need_node=0
fi
if [ "$need_node" = 1 ]; then
	echo "→ Node.js 22 (NodeSource)"
	curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
	sudo apt-get install -y nodejs
fi
echo "  node $(node --version), npm $(npm --version)"

# Python packages the bridges/vault use. PEP 668 blocks plain pip on Debian
# 12+; --user + --break-system-packages is the documented escape for a host
# you administer. Skip silently if pip is unavailable.
echo "→ python packages (detect-secrets for the vault scanner)"
python3 -m pip install --user --break-system-packages detect-secrets >/dev/null 2>&1 \
	|| python3 -m pip install --user detect-secrets >/dev/null 2>&1 \
	|| echo "  ~ detect-secrets not installed — unquoted \`vault set\` will be refused (see startup output)"

if [ "$WITH_CLI" = 1 ] && ! command -v claude >/dev/null 2>&1; then
	echo "→ Claude Code CLI (native installer → ~/.local/bin/claude)"
	curl -fsSL https://claude.ai/install.sh | bash
	case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *)
		echo "  add to your shell rc:  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
	esac
fi

echo
echo "Done. Next:"
echo "  1. claude login            (or: codex login)"
echo "  2. bash scripts/linux/install-headless-desktop.sh   # only on a server with no desktop"
echo "  3. ./start.sh"
