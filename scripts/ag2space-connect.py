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


def workspace_path(env: dict) -> Path:
    out = subprocess.run(["bash", str(REPO / "scripts" / "sutando-config.sh"), "workspace"],
                         capture_output=True, text=True, check=True, env=env)
    return Path(out.stdout.strip())


# The one bridge this may replace: this workspace's primary-lane lock holder.
# Never by argv — named lanes and other workspaces share it verbatim.
def primary_lock_path(ws: Path) -> Path:
    return ws / "state" / "locks" / "gateway-bridge.lock"


def lock_holder(ws: Path) -> "dict | None":
    try:
        d = json.loads(primary_lock_path(ws).read_text())
        return d if isinstance(d, dict) and isinstance(d.get("pid"), int) else None
    except (OSError, ValueError):
        return None


def bridge_markers() -> tuple[str, str]:
    return (str(REPO / "src" / "remote-gateway-bridge.py"), str(REPO / "src" / "remote-relay-bridge.py"))


def pid_is_this_checkouts_bridge(pid: int) -> bool:
    try:
        args = subprocess.run(["ps", "-o", "args=", "-p", str(pid)], capture_output=True, text=True).stdout
    except OSError:
        return False
    return any(m in args for m in bridge_markers())


def primary_bridge_pid(ws: Path) -> "int | None":
    """PID of this workspace's primary-lane bridge, or None when the lock is absent, stale, or foreign."""
    h = lock_holder(ws)
    if not h:
        return None
    pid = h["pid"]
    if pid == os.getpid() or not pid_is_this_checkouts_bridge(pid):
        return None
    return pid


def bridge_agent_id(pid: int) -> "str | None":
    """AGENT_ID from the bridge's environment where the OS exposes it (Linux); None elsewhere."""
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return None
    for item in raw.split(b"\0"):
        if item.startswith(b"AGENT_ID="):
            return item[len(b"AGENT_ID="):].decode(errors="replace").strip() or None
    return None


LAUNCHD_LABEL = "com.sutando.gateway-bridge"


def launchd_job(ws: Path, run=subprocess.run, config_dir: "str | None" = None) -> "dict | None":
    """The user's gateway launchd job. `ours` = it runs THIS checkout against THIS
    workspace; `stale` = ours, but its baked CLAUDE_CONFIG_DIR is not `config_dir`
    (the directory the new credentials were just written under), so a kickstart
    would reload the OLD channel .env and connect as the old agent."""
    if sys.platform != "darwin":
        return None
    svc = f"gui/{os.getuid()}/{LAUNCHD_LABEL}"
    r = run(["launchctl", "print", svc], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    text = r.stdout
    m = re.search(r"^\s*pid = (\d+)", text, re.M)
    wrapper = str(REPO / "src" / "launchd" / "gateway-bridge-wrapper.sh")
    args = _launchctl_block(text, "arguments")
    envb = _launchctl_block(text, "environment")
    def _env(key: str) -> str:
        for ln in envb:
            if ln.startswith(key) and ln[len(key):].lstrip().startswith("=>"):
                return ln.split("=>", 1)[-1].strip()
        return ""
    ours = (wrapper in args) and _env("SUTANDO_WORKSPACE") == str(ws)
    job_dir = _env("CLAUDE_CONFIG_DIR")
    stale = bool(ours and config_dir and _same_path(job_dir, config_dir) is False)
    return {"service": svc, "pid": int(m.group(1)) if m else None, "ours": ours,
            "config_dir": job_dir or None, "stale": stale}


def _same_path(a: str, b: str) -> bool:
    """Path equality after resolving symlinks; a missing or empty side is unequal."""
    if not a or not b:
        return False
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return a.rstrip("/") == b.rstrip("/")


def _launchctl_block(text: str, name: str) -> list[str]:
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip() == f"{name} = {{":
            out = []
            for raw in lines[i + 1:]:
                if raw.strip() == "}":
                    return out
                out.append(raw.strip())
    return []


def stop_running_bridge(env: dict, wait_s: float = 15.0, run=subprocess.run, kill=os.kill) -> dict:
    """Retire the primary-lane bridge of THIS workspace so the restart is real.

    Supervised (macOS, launchd job verified to run this checkout + workspace):
    record its pid, one `kickstart -k`, signal nothing — the job restarts
    itself and re-reads the env file. Unsupervised: SIGTERM the lock-holding
    pid (after checking its argv is this checkout's bridge), wait for exit.
    Named lanes, other workspaces and foreign launchd jobs are never touched.
    """
    import signal
    ws = workspace_path(env)
    old_pid = primary_bridge_pid(ws)
    job = launchd_job(ws, run=run, config_dir=env.get("CLAUDE_CONFIG_DIR"))
    if job and job["ours"] and job["stale"]:
        # A kickstart would reload the OLD baked CLAUDE_CONFIG_DIR; the installer
        # boots the job out and re-bootstraps it with the current directory.
        print(f"ag2space-connect: {LAUNCHD_LABEL} is baked to CLAUDE_CONFIG_DIR={job['config_dir']}, "
              f"not {env.get('CLAUDE_CONFIG_DIR')} — reinstalling the launchd job", file=sys.stderr)
        r = run(["bash", str(REPO / "src" / "install-gateway-bridge-launchd.sh"), "install"],
                capture_output=True, text=True, env=env)
        if r.returncode != 0:
            print(f"ag2space-connect: launchd reinstall failed (rc={r.returncode}): {(r.stderr or r.stdout or '').strip()[-400:]}",
                  file=sys.stderr)
            return {"mode": "launchd-stale", "old_pid": job["pid"] or old_pid}
        return {"mode": "launchd-reinstalled", "old_pid": job["pid"] or old_pid}
    if job and job["ours"]:
        run(["launchctl", "kickstart", "-k", job["service"]], capture_output=True)
        return {"mode": "launchd", "old_pid": job["pid"] or old_pid}
    if job and not job["ours"]:
        print(f"ag2space-connect: the {LAUNCHD_LABEL} launchd job belongs to another checkout/workspace — left alone",
              file=sys.stderr)
    if old_pid is None:
        return {"mode": "none", "old_pid": None}
    try:
        kill(old_pid, signal.SIGTERM)
    except ProcessLookupError:
        return {"mode": "none", "old_pid": None}
    deadline = time.time() + wait_s
    while time.time() < deadline and pid_is_this_checkouts_bridge(old_pid):
        time.sleep(0.5)
    if pid_is_this_checkouts_bridge(old_pid):
        try:
            kill(old_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return {"mode": "bare", "old_pid": old_pid}


def normalize_relay(url: str) -> str:
    """scheme://host[:port]/path — what the bridge persists in gateway-status.json."""
    from urllib.parse import urlsplit
    p = urlsplit(url.strip())
    host = (p.hostname or "").lower() + (f":{p.port}" if p.port else "")
    return f"{p.scheme.lower()}://{host}{p.path.rstrip('/')}"


def wait_for_new_bridge(env: dict, old_pid: "int | None", since: float, relay_url: str,
                        agent_id: "str | None" = None, wait_s: float = 90.0) -> str:
    """Return "" once the REPLACEMENT is serving the new connection, else why not (yet).

    Evidence required, all at once: this workspace's lock is held by a live
    bridge pid that is not the old one; gateway-status.json, which only the
    lock holder writes, is newer than the restart, `connected`, and names
    exactly this relay (full normalized URL, not a hostname); and that bridge
    positively reports serving the new agent — from /proc where the OS has it,
    else from the `agent_id` the bridge writes into its own status. An
    unknown identity is not evidence: on macOS the old configuration can
    reconnect to the same relay with a new pid and look identical otherwise.
    90 s: a just-minted bearer can take one long-poll (25 s) plus a 30 s auth
    re-check before the relay accepts it.
    """
    ws = workspace_path(env)
    status = ws / "state" / "gateway-status.json"
    want = normalize_relay(relay_url)
    deadline = time.time() + wait_s
    last = "no new gateway bridge process took this workspace's lock"
    while time.time() < deadline:
        pid = primary_bridge_pid(ws)
        if pid is None or pid == old_pid:
            last = "no new gateway bridge process took this workspace's lock"
            time.sleep(1); continue
        try:
            st = json.loads(status.read_text())
        except (OSError, ValueError):
            last = f"bridge pid {pid} is up but {status} is not readable yet"
            time.sleep(1); continue
        seen_agent = bridge_agent_id(pid) or (str(st.get("agent_id") or "").strip() or None)
        if agent_id and seen_agent and seen_agent != agent_id:
            last = f"bridge pid {pid} serves {seen_agent}, not {agent_id}"
        elif float(st.get("ts") or 0) < since:
            last = f"bridge pid {pid} is up but has not written status since the restart"
        elif normalize_relay(str(st.get("gateway") or "")) != want:
            last = f"bridge pid {pid} reports gateway {st.get('gateway')!r}, expected {want}"
        elif not st.get("connected"):
            last = f"bridge pid {pid} is not connected yet: {st.get('error') or 'waiting'}"
        elif agent_id and not seen_agent:
            last = f"bridge pid {pid} is connected but has not reported which agent it serves (expected {agent_id})"
        else:
            return ""
        time.sleep(1)
    return last + " (check workspace/logs/remote-gateway-bridge.log)"


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
    stopped = stop_running_bridge(senv) if replacing else {"mode": "none", "old_pid": None}
    if stopped["mode"] == "launchd-stale":
        print("ag2space-connect: the credential file is written, but the launchd job still runs the OLD "
              "CLAUDE_CONFIG_DIR and could not be reinstalled — the running bridge is NOT the new agent. "
              "Fix the install (bash src/install-gateway-bridge-launchd.sh install) and run bash scripts/restart-gateway-lanes.sh",
              file=sys.stderr)
        return 1
    if stopped["mode"] == "launchd":
        print(f"Bounced the launchd-supervised gateway bridge (was pid {stopped['old_pid']}).")
    elif stopped["mode"] == "launchd-reinstalled":
        print(f"Reinstalled the launchd-supervised gateway bridge for the current CLAUDE_CONFIG_DIR (was pid {stopped['old_pid']}).")
    elif stopped["mode"] == "bare":
        print(f"Stopped the previous gateway bridge (pid {stopped['old_pid']}).")
    since = time.time()
    rc = subprocess.run(["bash", str(REPO / "scripts" / "restart-gateway-lanes.sh")], env=senv).returncode
    if rc != 0:
        print(f"ag2space-connect: restart-gateway-lanes.sh exited {rc} — the env file is in place; run it again or bash src/startup.sh", file=sys.stderr)
        return 1
    why = wait_for_new_bridge(senv, stopped["old_pid"], since, poll["relay_url"], poll.get("agent_id"))
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
