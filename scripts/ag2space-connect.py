#!/usr/bin/env python3
"""Connect this Sutando to AG2 Space from a terminal — the device-authorization
flow the desktop app runs, for hosts without one (a Linux VM, a headless Mac).

Two modes:

  --label <name>   (recommended when you already own an agent) Log in with your
                   AG2 Space username + password at a prompt (read silently,
                   sent only to the AG2 Space API) and bind THIS host to the
                   agent `@<you>-<label>.agent`, creating it if needed. Without
                   a label the server reuses your first agent, so a second
                   host would share the first one's identity and queue.
  --browser        Open the printed URL in any browser and sign in there
                   (works with Google sign-in). No label is possible on that
                   page, so this shares your existing agent — fine for a
                   single host.

On success the gateway env file is written (0600) at the path startup.sh
reads — `channels/ag2space/.env` under the Claude config dir — and the
gateway lane is restarted so the bridge starts polling at once.

Usage:
  python3 scripts/ag2space-connect.py --label gce-linux
  python3 scripts/ag2space-connect.py --browser
  python3 scripts/ag2space-connect.py --label x --display-name "Sutando on GCE" --no-restart
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_API = "https://ag2.space/api"
# Mirrors AGENT_LABEL_RE in the AG2 Space provision API (connect_naming.py).
LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")


def api(base: str, path: str, body: dict, timeout: int = 30) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{base}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "user-agent": "sutando-ag2space-connect/1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {"error": str(e)}


def sutando_env() -> dict:
    """Process env with CLAUDE_CONFIG_DIR resolved exactly as startup.sh resolves it.

    Mirrors src/claude_config_dir.sh: the configured dir wins whenever the
    helper is readable — an exported value (a stale shell, another checkout)
    is NOT honoured, because startup will not honour it either and the file
    would land where no bridge reads it. Only a missing helper falls back to
    the caller's export.
    """
    env = dict(os.environ)
    helper = REPO / "scripts" / "sutando-config.sh"
    if os.access(helper, os.R_OK):
        out = subprocess.run(["bash", str(helper), "claude-sutando-config-dir"],
                             capture_output=True, text=True)
        if out.returncode != 0:
            raise SystemExit(f"ag2space-connect: claude_sutando_config_dir invalid — refusing: {out.stderr.strip()}")
        env["CLAUDE_CONFIG_DIR"] = out.stdout.strip()
    elif not env.get("CLAUDE_CONFIG_DIR"):
        raise SystemExit(f"ag2space-connect: {helper} unreadable and no CLAUDE_CONFIG_DIR exported — refusing")
    return env


def bridge_pids() -> list[int]:
    """PIDs of gateway bridges launched from THIS checkout (either entry name)."""
    pids: list[int] = []
    for marker in ("src/remote-gateway-bridge.py", "src/remote-relay-bridge.py"):
        out = subprocess.run(["pgrep", "-f", str(REPO / marker)], capture_output=True, text=True)
        pids += [int(x) for x in out.stdout.split() if x.isdigit() and int(x) != os.getpid()]
    return sorted(set(pids))


def stop_running_bridge(env: dict, wait_s: float = 15.0) -> list[int]:
    """Stop the bridge that holds the OLD connection so the restart is real.

    restart-gateway-lanes.sh only spawns; the bridge's singleton lock makes the
    new process defer to a live old one, which would keep polling the previous
    agent. On macOS a launchd-supervised job is bounced with kickstart -k
    (it re-reads the env file on launch); a bare process is SIGTERMed and
    waited for. The task watcher is never touched.
    """
    import signal
    if sys.platform == "darwin":
        svc = f"gui/{os.getuid()}/com.sutando.gateway-bridge"
        if subprocess.run(["launchctl", "print", svc], capture_output=True).returncode == 0:
            subprocess.run(["launchctl", "kickstart", "-k", svc], capture_output=True)
    victims = bridge_pids()
    for pid in victims:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.time() + wait_s
    while time.time() < deadline and any(p in bridge_pids() for p in victims):
        time.sleep(0.5)
    left = [p for p in victims if p in bridge_pids()]
    for pid in left:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return victims


def gateway_status_path(env: dict) -> Path:
    out = subprocess.run(["bash", str(REPO / "scripts" / "sutando-config.sh"), "workspace"],
                         capture_output=True, text=True, check=True, env=env)
    return Path(out.stdout.strip()) / "state" / "gateway-status.json"


def wait_for_new_bridge(env: dict, old_pids: list[int], since: float, relay_url: str, wait_s: float = 90.0) -> str:
    """Return "" when a fresh bridge is serving the new relay, else the reason it is not (yet).

    90 s: a just-minted bearer can take one poll to propagate to the relay (a 401 the
    bridge retries after 30 s), so the wait must outlast at least one re-check.
    """
    status = gateway_status_path(env)
    host = relay_url.split("//", 1)[-1].split("/", 1)[0]
    deadline = time.time() + wait_s
    seen_pid = False
    while time.time() < deadline:
        live = [p for p in bridge_pids() if p not in old_pids]
        seen_pid = seen_pid or bool(live)
        try:
            st = json.loads(status.read_text())
            if float(st.get("ts") or 0) >= since and st.get("connected") and host in str(st.get("gateway") or ""):
                return ""
        except (OSError, ValueError):
            pass
        time.sleep(1)
    if not seen_pid:
        return "no new gateway bridge process appeared"
    return f"bridge started but {status} has not reported connected to {host} yet (check workspace/logs/remote-gateway-bridge.log)"


def env_path(env: dict) -> Path:
    out = subprocess.run(
        ["bash", str(REPO / "scripts" / "sutando-config.sh"), "claude-home-path", "channels/ag2space/.env"],
        capture_output=True, text=True, check=True, env=env,
    )
    return Path(out.stdout.strip())


def write_env(path: Path, *, agent_id: str, relay_url: str, bearer: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        f"AGENT_ID={agent_id}\n"
        f"REMOTE_TASK_URL={relay_url}\n"
        "REMOTE_TASK_PROVIDER=ag2space\n"
        f"REMOTE_TASK_TOKEN={bearer}\n"
        "REMOTE_TASK_TIER=owner\n"
    )
    tmp = path.with_suffix(".env.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(body)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def login_prompt(base: str) -> "str | None":
    """Prompt for username + password, exchange them for a user_session_token; None on failure."""
    # The login endpoint takes the Matrix localpart only — for @sutando-anas
    # (or @anas-…) that is "anas"; an email or a full @user:server is refused.
    username = input("AG2 Space username (the part before the colon in your Matrix id, e.g. anas): ").strip().lower()
    if username.startswith("@"):
        username = username[1:]
    if "@" in username:
        print("ag2space-connect: use your AG2 Space username, not an email — for @sutando-anas:ag2.space it is 'anas'", file=sys.stderr)
        return None
    username = username.split(":", 1)[0]
    password = getpass.getpass("AG2 Space password: ")
    if not username or not password:
        print("ag2space-connect: username and password are required", file=sys.stderr)
        return None
    st, login = api(base, "/user-login", {"username": username, "password": password})
    del password
    if st != 200 or not login.get("user_session_token"):
        print(f"ag2space-connect: login failed ({st}): {login.get('error', 'no session token')}", file=sys.stderr)
        return None
    return login["user_session_token"]


def default_label() -> str:
    host = socket.gethostname().split(".")[0].lower()
    host = re.sub(r"[^a-z0-9-]+", "-", host).strip("-")[:32].strip("-")
    return host or "sutando"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--label", help="agent label for THIS host (lowercase alnum + hyphens, <=32); creates @<you>-<label>.agent")
    mode.add_argument("--browser", action="store_true", help="sign in on the web page instead (no label; reuses your first agent)")
    ap.add_argument("--display-name", help="editable display name for a newly created agent")
    ap.add_argument("--api", default=os.environ.get("AG2_SPACE_API", DEFAULT_API), help=f"provision API base (default {DEFAULT_API})")
    ap.add_argument("--env-file", help="override the gateway env path (default: channels/ag2space/.env under the config dir)")
    ap.add_argument("--no-restart", action="store_true", help="write the env file but do not restart the gateway lane")
    ap.add_argument("--force", action="store_true", help="overwrite an existing gateway env file")
    args = ap.parse_args()

    senv = sutando_env()
    if args.env_file and not args.no_restart:
        print("ag2space-connect: --env-file needs --no-restart — the launcher only reads channels/ag2space/.env "
              "under the config dir, so a custom file cannot be started from here", file=sys.stderr)
        return 2
    target = Path(args.env_file) if args.env_file else env_path(senv)
    replacing = target.exists()
    if replacing and not args.force:
        print(f"ag2space-connect: {target} already exists — this host is connected. Re-run with --force to replace it.", file=sys.stderr)
        return 2

    base = args.api.rstrip("/")
    session_token = None
    label = None
    if not args.browser:
        label = args.label or default_label()
        if not LABEL_RE.match(label):
            print(f"ag2space-connect: invalid label '{label}' — lowercase letters, digits and internal hyphens, up to 32 chars", file=sys.stderr)
            return 2
        print(f"Binding this host to agent label '{label}' on {base}.")
        # Credentials first: the device code lives 15 minutes, and typing at
        # the prompts must not eat into that.
        session_token = login_prompt(base)
        if session_token is None:
            return 1

    st, start = api(base, "/connect/start", {})
    if st != 200 or not start.get("device_code"):
        print(f"ag2space-connect: /connect/start failed ({st}): {start.get('error', start)}", file=sys.stderr)
        return 1
    code = start["device_code"]

    if args.browser:
        print("Open this URL in a browser and sign in to link this host:\n\n  " + start["verify_url"] + "\n")
        print("Waiting for the sign-in to complete (Ctrl-C to abort)…")
    else:
        body = {"device_code": code, "user_session_token": session_token, "label": label}
        if args.display_name:
            body["display_name"] = args.display_name.strip()[:100]
        st, done = api(base, "/connect/complete", body, timeout=90)
        if st != 200 or not done.get("ok"):
            print(f"ag2space-connect: /connect/complete failed ({st}): {done.get('error', done)}", file=sys.stderr)
            return 1
        print(f"Linked agent {done.get('agent_id')}; collecting the gateway credential…")

    interval = max(2, int(start.get("interval", 3)))
    deadline = time.time() + int(start.get("expires_in", 900))
    while time.time() < deadline:
        st, poll = api(base, "/connect/poll", {"device_code": code})
        if st == 200 and poll.get("bearer"):
            break
        if st not in (200, 202):
            print(f"ag2space-connect: /connect/poll failed ({st}): {poll.get('error', poll)}", file=sys.stderr)
            return 1
        time.sleep(interval)
    else:
        print("ag2space-connect: the device code expired before the sign-in completed — run again", file=sys.stderr)
        return 1

    write_env(target, agent_id=poll["agent_id"], relay_url=poll["relay_url"].rstrip("/"), bearer=poll["bearer"])
    print(f"Wrote {target} (mode 600): AGENT_ID={poll['agent_id']} REMOTE_TASK_URL={poll['relay_url']}")

    if args.no_restart:
        print("Gateway lane not restarted (--no-restart). Start it with: bash scripts/restart-gateway-lanes.sh")
        return 0
    # A replaced connection must replace the RUNNING bridge too, or the old
    # process keeps polling the previous agent while this prints success.
    old_pids = stop_running_bridge(senv) if replacing else []
    if old_pids:
        print(f"Stopped the previous gateway bridge (pid {', '.join(map(str, old_pids))}).")
    since = time.time()
    rc = subprocess.run(["bash", str(REPO / "scripts" / "restart-gateway-lanes.sh")], env=senv).returncode
    if rc != 0:
        print(f"ag2space-connect: restart-gateway-lanes.sh exited {rc} — the env file is in place; run it again or bash src/startup.sh", file=sys.stderr)
        return 1
    why = wait_for_new_bridge(senv, old_pids, since, poll["relay_url"])
    if why:
        print(f"ag2space-connect: env file written but the connection is PENDING — {why}", file=sys.stderr)
        return 1
    print(f"Connected. Open AG2 Space and DM {poll['agent_id']} — the reply comes from this host.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\naborted", file=sys.stderr)
        sys.exit(130)
