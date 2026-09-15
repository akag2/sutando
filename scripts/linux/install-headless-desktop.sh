#!/usr/bin/env bash
# Give a Linux server a persistent virtual desktop Sutando can see and drive.
#
# A cloud VM has no monitor, so there is no $DISPLAY for screenshots or
# keystrokes. This installs four systemd units for the invoking user:
#
#   sutando-xvfb     Xvfb :99          the virtual X server (1920x1080)
#   sutando-desktop  XFCE on :99       a real session: panel, file manager, browser
#   sutando-x11vnc   VNC on 127.0.0.1:5900   look at / control the desktop
#   sutando-novnc    noVNC on 127.0.0.1:6080  same, from a browser
#
# VNC and noVNC bind LOOPBACK ONLY and run without a password: reach them over
# an SSH (or cloud IAP) tunnel, which is what authenticates you. Do not open
# 5900/6080 on a public interface.
#
# After this, log in as the same user with DISPLAY=:99 (the profile snippet
# below sets it) and run ./start.sh — screen capture and input control light
# up in the startup transcript.
#
# Usage: bash scripts/linux/install-headless-desktop.sh [--display :99] [--geometry 1920x1080]
set -euo pipefail

DISPLAY_NUM=":99"
GEOMETRY="1920x1080"
while [ $# -gt 0 ]; do
	case "$1" in
		--display)  DISPLAY_NUM="$2"; shift 2 ;;
		--geometry) GEOMETRY="$2"; shift 2 ;;
		-h|--help)  sed -n '2,22p' "$0"; exit 0 ;;
		*) echo "install-headless-desktop: unknown arg $1" >&2; exit 2 ;;
	esac
done

if [ "$(id -u)" = 0 ]; then
	echo "install-headless-desktop: run as the user Sutando will run as (sudo is used internally)" >&2
	exit 2
fi
command -v apt-get >/dev/null 2>&1 || { echo "install-headless-desktop: apt-get not found — Debian/Ubuntu only" >&2; exit 2; }
command -v systemctl >/dev/null 2>&1 || { echo "install-headless-desktop: systemd required" >&2; exit 2; }

USER_NAME="$(id -un)"
USER_ID="$(id -u)"
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)"

export DEBIAN_FRONTEND=noninteractive
echo "→ apt: xvfb, xfce4, x11vnc, novnc, chromium"
sudo apt-get update -y
sudo apt-get install -y xvfb x11vnc novnc websockify xfce4 xfce4-terminal dbus-x11 chromium \
	fonts-noto fonts-noto-color-emoji x11-utils

# /run/user/<uid> must exist for a system service running as this user.
sudo loginctl enable-linger "$USER_NAME" || true

unit() { sudo tee "/etc/systemd/system/$1" >/dev/null; }

unit sutando-xvfb.service <<UNIT
[Unit]
Description=Xvfb $DISPLAY_NUM virtual display for Sutando
After=network.target
[Service]
User=$USER_NAME
ExecStart=/usr/bin/Xvfb $DISPLAY_NUM -screen 0 ${GEOMETRY}x24 -nolisten tcp -ac
Restart=always
RestartSec=2
[Install]
WantedBy=multi-user.target
UNIT

unit sutando-desktop.service <<UNIT
[Unit]
Description=XFCE session on $DISPLAY_NUM for Sutando
After=sutando-xvfb.service
Requires=sutando-xvfb.service
[Service]
User=$USER_NAME
Environment=DISPLAY=$DISPLAY_NUM
Environment=HOME=$USER_HOME
Environment=XDG_RUNTIME_DIR=/run/user/$USER_ID
ExecStartPre=/bin/sh -c 'for i in \$(seq 1 30); do xdpyinfo -display $DISPLAY_NUM >/dev/null 2>&1 && exit 0; sleep 1; done; exit 1'
ExecStart=/usr/bin/dbus-run-session -- /usr/bin/startxfce4
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
UNIT

unit sutando-x11vnc.service <<UNIT
[Unit]
Description=x11vnc for the Sutando virtual desktop (loopback only — reach via ssh tunnel)
After=sutando-desktop.service
Requires=sutando-xvfb.service
[Service]
User=$USER_NAME
ExecStart=/usr/bin/x11vnc -display $DISPLAY_NUM -forever -shared -localhost -rfbport 5900 -nopw -noxdamage -xkb -quiet
Restart=always
RestartSec=2
[Install]
WantedBy=multi-user.target
UNIT

unit sutando-novnc.service <<UNIT
[Unit]
Description=noVNC web front for the Sutando virtual desktop (loopback only)
After=sutando-x11vnc.service
[Service]
User=$USER_NAME
ExecStart=/usr/bin/websockify --web=/usr/share/novnc/ 127.0.0.1:6080 127.0.0.1:5900
Restart=always
RestartSec=2
[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now sutando-xvfb sutando-desktop sutando-x11vnc sutando-novnc

# Make the display the default for this user's shells, so `./start.sh` over
# SSH finds it without an export each time.
snippet="$USER_HOME/.profile"
if ! grep -q 'DISPLAY=.*sutando-headless-desktop' "$snippet" 2>/dev/null; then
	printf '\nexport DISPLAY="${DISPLAY:-%s}"  # sutando-headless-desktop\nexport BROWSER=chromium\n' "$DISPLAY_NUM" >> "$snippet"
fi
DISPLAY="$DISPLAY_NUM" xdg-mime default chromium.desktop x-scheme-handler/http x-scheme-handler/https text/html 2>/dev/null || true

echo
echo "Virtual desktop is up on $DISPLAY_NUM."
echo "  view it:   ssh -L 5900:127.0.0.1:5900 -L 6080:127.0.0.1:6080 <host>"
echo "             then open vnc://localhost:5900  or  http://localhost:6080/vnc.html"
echo "  status:    systemctl status sutando-xvfb sutando-desktop sutando-x11vnc sutando-novnc"
echo "  then:      export DISPLAY=$DISPLAY_NUM; ./start.sh"
