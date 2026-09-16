# Sutando on Linux (Debian / Ubuntu)

Sutando runs on a Linux host with an X11 desktop — a real one, or a virtual
one on a cloud VM. This page is the Linux counterpart of the macOS quick start
in `README.md`: what is supported, what is not, how to install, and how the
macOS-only pieces degrade.

Tested on Debian 12 (bookworm) with XFCE on TigerVNC's Xvnc (GCE `e2-standard-4`); see the
status legend below for what "tested" covers per surface.
Ubuntu 22.04+ should behave the same; other distros only change the install
hints (`scripts/platform.sh` maps package names).

## What works

Status legend — **verified**: exercised on the Debian 12 test VM; **implemented**:
has a Linux code path and unit tests for the commands it builds, not yet exercised
live. Treat "implemented" rows as beta until someone reports them working.

| Surface | Linux status | Backend |
|---|---|---|
| Boot (`startup.sh`), dashboard `:7844`, agent API `:7843`, web client `:8080` | verified | unchanged (Python / Node) |
| Core agent session (Claude Code in tmux) | verified — logged in; AG2 Space DM round trips witnessed (owner's and an independent reviewer's, replies in 11–18 s) | unchanged — `src/agent/start-cli.sh` |
| Task watcher (`tasks/` → core) | verified — gateway tasks reached the core and results were delivered back | `fswatch` |
| Screen capture `:7845` — still frames, `/displays`, downscale | verified | `scrot` / ImageMagick `import`; resize via `mogrify` |
| Screen recording (`/capture-video`) | verified — start/stop, output decodes (h264 + aac) | `ffmpeg -f x11grab` (+ PulseAudio only when `pactl info` answers) → `.mp4` |
| `press_key`, `type_text`, `switch_app` | verified (`xdotool` typing into a desktop window) | `xdotool` |
| Pending-question desktop notification (`check-pending-questions.py`) | implemented — `notify-send` when present; reported as FAILED (never a crash) without it, the voice/DM paths still run | libnotify |
| `clipboard` | implemented | `xclip` (or `xsel`) |
| `open_file`, dashboard auto-open | implemented | `xdg-open` (a missing opener is reported to the caller, never crashes the service) |
| `volume` | implemented | `pactl` (or `amixer`) |
| Browser voice (Gemini Live) | implemented — needs `GEMINI_API_KEY`; not exercised on Linux | unchanged |
| Telegram / Discord / Slack bridges | implemented — run directly, no supervisor | unchanged |
| Credential proxy `:7846` (quota tracking) | verified to read the Linux credential file; token injection needs a logged-in CLI | `<CLAUDE_CONFIG_DIR>/.credentials.json` |
| Vault (`vault set KEY VALUE`) | implemented — needs `secret-tool` + a Secret Service; refuses loudly otherwise | libsecret |
| Phone (Twilio + ngrok) | implemented | unchanged |

## What does not work on Linux (yet)

These are macOS-specific by construction and return a clear error instead of
crashing the service:

- **Menu-bar app** (`--with-app`, `scripts/install-menu-bar-app.sh`) — Swift/AppKit. The installer exits 0 with "macOS only".
- **launchd supervision** of voice-agent, web-client, bridges, credential proxy — services are started directly by `startup.sh`; nothing restarts them on crash. Use the systemd units from `scripts/linux/install-headless-desktop.sh` as a template if you need supervision.
- **Browser control tools** in `src/browser-tools.ts` — `open_url`, `switch_tab`, `close_tab`, `scroll`, `click` and the slide-deck controls drive Google Chrome through AppleScript and return the `osascript` spawn error on Linux. `describe_screen` is different: it goes through the capture server and the vision service, not AppleScript, so it should work on Linux but is **unverified** there. To show a page, `open_file` on a local `.html` file opens it in the default browser via `xdg-open` (the tool checks the path exists, so an `http://` URL is not accepted).
- **Zoom / Google Meet joining** (`skills/zoom`, `src/meeting-tools.ts`) — drives the macOS Zoom app.
- **Calendar, Reminders, Contacts, iMessage, Notes** recipes in `docs/built-in-tools.md` — AppleScript / EventKit.
- **Multi-display** — one X screen per `$DISPLAY`; `display=N` selectors all resolve to it.
- **Sleep inhibit** uses `xset s off -dpms` (X screensaver) rather than `caffeinate`; a VM never sleeps anyway.

## Install

```bash
git clone https://github.com/sonichi/sutando.git
cd sutando
bash scripts/linux/install-debian-deps.sh      # apt packages, Node 22 (NodeSource), Claude Code CLI
claude login                                   # or: codex login
cp .env.example .env                           # add GEMINI_API_KEY for voice (optional)
./start.sh
```

`install-debian-deps.sh` installs exactly what `docs/runtime-dependencies.md`
lists, plus the X11 tools the Linux backend uses:

| Purpose | apt packages |
|---|---|
| boot | `nodejs` (NodeSource 22.x), `python3`, `fswatch`, `lsof`, `tmux`, `procps` |
| screen capture / recording | `scrot`, `imagemagick`, `ffmpeg`, `x11-utils` |
| input + clipboard + open | `xdotool`, `xclip`, `xsel` (`xdg-open` ships with the desktop) |
| notifications, volume, sleep | `libnotify-bin`, `pulseaudio-utils`, `x11-xserver-utils` |
| vault (optional) | `libsecret-tools` + a running Secret Service (gnome-keyring) |

## Headless server / cloud VM

A VM has no monitor, so `$DISPLAY` is unset and every desktop surface reports
itself off at boot ("⚠ Screen capture unavailable — no $DISPLAY"). Give it a
persistent virtual desktop:

```bash
bash scripts/linux/install-headless-desktop.sh
```

That installs three systemd units for your user — `Xvnc :99` (TigerVNC's X
server, which also serves VNC on `127.0.0.1:5900`), an XFCE session on it, and
noVNC on `127.0.0.1:6080` — and adds `export DISPLAY=:99` to your `~/.profile`.
VNC binds loopback only and has no password: the SSH (or cloud IAP) tunnel is
the authentication.

Why Xvnc and not Xvfb + x11vnc: in the original Xvfb + XFCE + x11vnc session on
the test VM, GTK3 windows (the AG2 Space desktop app, a bare WebKitGTK page, even
the XFCE panel menu) received pointer motion and key presses but no mouse button
presses, over VNC and from `xdotool` alike, and the same test on an Xvnc display
passed. A fresh, bare Xvfb started later did deliver clicks, and so did a
fresh Xvfb + XFCE + x11vnc install on a second host, so the cause was a state
of that one session rather than an Xvfb defect and remains unexplained. Xvnc
is kept because it was verified working end to end, it is one process instead
of two, and the installer migration (old units → Xvnc, re-run, display and
geometry change) was exercised on a disposable host.

**Trust boundary.** This installer assumes a single-user host you administer
(a personal VM). Xvnc runs with `-ac` and without a VNC password, so any
local process or account on the machine can see and drive the desktop — the
same access the Sutando core itself has. Do not use it on a shared host; there
you want an authenticated VNC (`Xvnc -SecurityTypes VncAuth -PasswordFile`), an Xauthority-protected
display, and a dedicated account.

```bash
# from your laptop
ssh -L 5900:127.0.0.1:5900 -L 6080:127.0.0.1:6080 -L 8080:127.0.0.1:8080 -L 7844:127.0.0.1:7844 <host>
open vnc://localhost:5900              # macOS Screen Sharing
# or http://localhost:6080/vnc.html    # any browser
# Sutando web UI: http://localhost:8080   dashboard: http://localhost:7844
```

Then, on the host, as the same user: `export DISPLAY=:99; ./start.sh`. The
startup transcript should show `✓ Screen capture (scrot on :99)` and
`✓ Input control (xdotool on :99)`.

## Connect to AG2 Space

The AG2 Space link is the gateway bridge (`src/remote-gateway-bridge.py`,
[remote-gateway-protocol.md](remote-gateway-protocol.md)); on Linux it starts
directly from `startup.sh` (no launchd) as soon as `channels/ag2space/.env`
holds a token. The desktop app's Connect flow writes that file on macOS; on a
Linux host use the terminal equivalent:

```bash
python3 scripts/ag2space-connect.py --label gce-linux     # username + password prompt
python3 scripts/ag2space-connect.py --browser             # sign in on a web page (Google OK)
```

Give a **label** when you already own an agent (a Mac running Sutando): it
binds this host to `@<you>-<label>.agent`, creating it if needed. Without a
label the server reuses your first agent, so two hosts would share one
identity and one task queue. `--browser` cannot pass a label, so it is for a
single host or Google-only accounts. The script writes the env file (0600) and
restarts the gateway lane; the core must be logged in (`claude login`) for the
DM you send in AG2 Space to be answered.

## How the port works

The platform decision lives in three files; everything else asks them:

- `scripts/platform.sh` — shell: `sutando_os`, `sutando_is_linux`, `sutando_install_hint <tool>`, `sutando_has_display`, `sutando_has_launchd`.
- `src/startup-platform.sh` — the four boot steps that were Darwin-only (sleep inhibit, screen-capture probe, accessibility/xdotool probe, fswatch check), each with a Darwin branch that is the old behaviour verbatim.
- `src/platform.ts` + `src/platform-desktop.ts` — TypeScript: the supported-platform gate and the per-tool command *plans* (`open`/`xdg-open`, `osascript`/`xdotool`, `pbcopy`/`xclip`, …). Plans are pure and unit-tested; the inline tools only call `runPlan`.

`src/screen-capture-server.py` and `src/vault_intercept.py` branch on
`sys.platform` internally and keep their HTTP / function contracts unchanged.

## Troubleshooting

- **`✗ lsof not found`** — `sudo apt-get install -y lsof`. Every port guard in `startup.sh` uses it.
- **`⚠ Screen capture unavailable — no $DISPLAY`** — you are in a plain SSH shell. `export DISPLAY=:99` (after the headless-desktop install) or run inside the desktop session.
- **`scrot: Can't open X display`** — the X server is not up: `systemctl status sutando-xvnc`.
- **`vault: no secret store on this host`** — install `libsecret-tools` and run `gnome-keyring-daemon` in the session, or keep secrets in `.env` for now.
- **Dashboard did not auto-open** — `start.sh` only opens a browser when `xdg-open` and a `$DISPLAY` exist; otherwise open `http://localhost:7844` yourself (through the tunnel on a VM).
