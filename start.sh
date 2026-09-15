#!/usr/bin/env bash
# One-command entry point: menu bar app + core + dashboard in the browser.
# Thin by design — src/startup.sh stays the supported low-level entry.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD_URL="${SUTANDO_DASHBOARD_URL:-http://localhost:7844}"

# Backgrounded: startup.sh ends in `exec`, so anything sequenced after it never
# runs. Poll rather than sleep — the port is ready long before a fixed guess.
# `open` is macOS; `xdg-open` is the freedesktop equivalent (needs a $DISPLAY).
# Neither present → headless host, skip the browser exactly as before.
OPEN_CMD=""
if command -v open >/dev/null 2>&1; then OPEN_CMD="open"
elif command -v xdg-open >/dev/null 2>&1 && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then OPEN_CMD="xdg-open"
fi
if [ "${SUTANDO_OPEN_DASHBOARD:-1}" = "1" ] && [ -n "$OPEN_CMD" ]; then
    (
        for _ in $(seq 1 60); do
            if curl -fsS -o /dev/null --max-time 1 "$DASHBOARD_URL" 2>/dev/null; then
                "$OPEN_CMD" "$DASHBOARD_URL" >/dev/null 2>&1 &
                exit 0
            fi
            sleep 1
        done
        echo "start.sh: dashboard did not answer at $DASHBOARD_URL within 60s;" >&2
        echo "  the core is still starting — open it manually once it's up." >&2
    ) &
fi

exec bash "$REPO/src/startup.sh" --with-app "$@"
