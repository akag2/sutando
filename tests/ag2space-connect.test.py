#!/usr/bin/env python3
"""scripts/ag2space-connect.py — the terminal AG2 Space connect flow.

Pins the three defects the round-2 review reproduced and the label contract:
the env file lands where startup.sh reads it even when a stale
CLAUDE_CONFIG_DIR is exported (shared-resolver precedence); `--env-file`
without `--no-restart` is refused instead of reporting a connection nothing
starts; replacing a connection stops the bridge process that holds the old
one; and `--label` reaches /connect/complete. The API is a local HTTP stub,
so no request leaves the machine and no credential is real.
"""
from __future__ import annotations

import http.server
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "ag2space-connect.py"
spec = importlib.util.spec_from_file_location("ag2space_connect", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class _Api(http.server.BaseHTTPRequestHandler):
    seen: list[tuple[str, dict]] = []
    polls = 0

    def log_message(self, *_):  # noqa: D401 - silence
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0)) or b"{}"))
        _Api.seen.append((self.path, body))
        if self.path == "/api/connect/start":
            out, st = {"device_code": "dc-1", "verify_url": "http://x/connect-page?code=dc-1", "interval": 1, "expires_in": 30}, 200
        elif self.path == "/api/user-login":
            out, st = ({"user_session_token": "sess-1"}, 200) if body.get("password") == "pw" else ({"error": "invalid_credentials"}, 401)
        elif self.path == "/api/connect/complete":
            out, st = {"ok": True, "agent_id": f"@u-{body.get('label')}.agent:x"}, 200
        elif self.path == "/api/connect/poll":
            _Api.polls += 1
            out, st = ({"status": "pending"}, 202) if _Api.polls < 2 else \
                ({"relay_url": "https://relay.test/relay", "bearer": "b" * 48, "agent_id": "@u-gce.agent:x"}, 200)
        else:
            out, st = {"error": "nope"}, 404
        data = json.dumps(out).encode()
        self.send_response(st); self.send_header("content-type", "application/json"); self.end_headers(); self.wfile.write(data)


def _serve():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Api)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/api"


def _run_main(argv: list[str], inputs: str = "u\n", password: str = "pw") -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with patch.object(sys, "argv", ["ag2space-connect.py", *argv]), \
            patch.object(sys, "stdin", io.StringIO(inputs)), \
            patch.object(mod.getpass, "getpass", return_value=password), \
            redirect_stdout(out), redirect_stderr(err):
        try:
            rc = mod.main()
        except SystemExit as e:
            rc = int(e.code or 0)
    return rc, out.getvalue(), err.getvalue()


class ConfigDirPrecedence(unittest.TestCase):
    def test_exported_stale_dir_is_overridden_by_the_shared_resolver(self):
        expected = subprocess.run(["bash", str(REPO / "scripts" / "sutando-config.sh"), "claude-sutando-config-dir"],
                                  capture_output=True, text=True, check=True).stdout.strip()
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/nonexistent/stale-shell"}):
            env = mod.sutando_env()
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], expected)
        self.assertTrue(str(mod.env_path(env)).startswith(expected))


class Flow(unittest.TestCase):
    def setUp(self):
        _Api.seen, _Api.polls = [], 0
        self.srv, self.api = _serve()
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        self.srv.shutdown()

    def test_label_flow_writes_0600_env_and_forwards_label(self):
        target = self.tmp / "channels" / "ag2space" / ".env"
        rc, out, err = _run_main(["--label", "gce", "--api", self.api, "--env-file", str(target), "--no-restart"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        text = target.read_text()
        self.assertIn("REMOTE_TASK_TOKEN=" + "b" * 48, text)
        self.assertIn("REMOTE_TASK_URL=https://relay.test/relay", text)
        self.assertIn("AGENT_ID=@u-gce.agent:x", text)
        complete = [b for p, b in _Api.seen if p == "/api/connect/complete"]
        self.assertEqual(complete[0]["label"], "gce")
        # Credentials are exchanged BEFORE the device code is minted, so the
        # 15-minute code TTL is not spent at the prompts (the 404 seen live).
        paths = [p for p, _ in _Api.seen]
        self.assertLess(paths.index("/api/user-login"), paths.index("/api/connect/start"))
        self.assertEqual(complete[0]["user_session_token"], "sess-1")
        self.assertNotIn("pw", json.dumps(_Api.seen).replace('"password": "pw"', ""), "password only ever goes to /user-login")

    def test_bad_password_is_reported_not_written(self):
        target = self.tmp / ".env"
        rc, _, err = _run_main(["--label", "gce", "--api", self.api, "--env-file", str(target), "--no-restart"], password="wrong")
        self.assertEqual(rc, 1)
        self.assertIn("login failed", err)
        self.assertFalse(target.exists())

    def test_env_file_without_no_restart_is_refused_before_any_request(self):
        rc, _, err = _run_main(["--label", "gce", "--api", self.api, "--env-file", str(self.tmp / "x.env")])
        self.assertEqual(rc, 2)
        self.assertIn("--no-restart", err)
        self.assertEqual(_Api.seen, [])

    def test_invalid_label_refused(self):
        rc, _, err = _run_main(["--label=-Bad-", "--api", self.api, "--env-file", str(self.tmp / "x.env"), "--no-restart"])
        self.assertEqual(rc, 2)
        self.assertIn("invalid label", err)

    def test_existing_file_needs_force(self):
        target = self.tmp / ".env"; target.write_text("old")
        rc, _, err = _run_main(["--label", "gce", "--api", self.api, "--env-file", str(target), "--no-restart"])
        self.assertEqual(rc, 2)
        self.assertIn("--force", err)
        self.assertEqual(target.read_text(), "old")


class BridgeReplacement(unittest.TestCase):
    """Every case runs against a TEMPORARY checkout identity and workspace, so a
    live bridge from the real checkout can never be selected, let alone signaled."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.checkout = self.tmp / "checkout"; (self.checkout / "src").mkdir(parents=True)
        self.ws = self.tmp / "workspace"; (self.ws / "state" / "locks").mkdir(parents=True)
        self._repo = mod.REPO
        mod.REPO = self.checkout
        self.p_ws = patch.object(mod, "workspace_path", return_value=self.ws); self.p_ws.start()
        self.procs = []

    def tearDown(self):
        mod.REPO = self._repo
        self.p_ws.stop()
        for pr in self.procs:
            if pr.poll() is None:
                pr.kill()

    def _standin(self):
        marker = str(self.checkout / "src" / "remote-gateway-bridge.py")
        pr = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", marker])
        self.procs.append(pr); time.sleep(0.3)
        return pr

    def _lock(self, pid: int, name="gateway-bridge"):
        (self.ws / "state" / "locks" / f"{name}.lock").write_text(json.dumps({"role": name, "pid": pid, "host": "h", "workspace": str(self.ws)}))

    def test_stop_targets_only_this_workspaces_primary_lock_holder(self):
        primary, named_lane = self._standin(), self._standin()
        self._lock(primary.pid)
        self._lock(named_lane.pid, "gateway-bridge.dev")   # same argv, different lane
        with patch.object(mod.sys, "platform", "linux"):
            out = mod.stop_running_bridge({}, wait_s=5)
        self.assertEqual(out, {"mode": "bare", "old_pid": primary.pid})
        self.assertIsNotNone(primary.wait(timeout=5))
        self.assertIsNone(named_lane.poll(), "a named lane with identical argv must survive")

    def test_stale_or_foreign_lock_is_not_a_kill_target(self):
        self._lock(999999)                       # no such process
        with patch.object(mod.sys, "platform", "linux"):
            self.assertEqual(mod.stop_running_bridge({}, wait_s=1)["mode"], "none")
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "/elsewhere/src/remote-gateway-bridge.py"])
        self.procs.append(other); time.sleep(0.3)
        self._lock(other.pid)                     # lock names a pid that is NOT this checkout's bridge
        with patch.object(mod.sys, "platform", "linux"):
            self.assertEqual(mod.stop_running_bridge({}, wait_s=1)["mode"], "none")
        self.assertIsNone(other.poll())

    def _launchctl_print(self, wrapper: str, ws: str, pid: int) -> str:
        return (f"gui/501/{mod.LAUNCHD_LABEL} = {{\n\tpid = {pid}\n\targuments = {{\n\t\t/bin/bash\n\t\t{wrapper}\n\t}}\n"
                f"\tenvironment = {{\n\t\tSUTANDO_WORKSPACE => {ws}\n\t\tHOME => /x\n\t}}\n}}\n")

    def test_macos_supervised_path_records_old_pid_then_kickstarts_and_signals_nothing(self):
        calls, kills = [], []
        wrapper = str(self.checkout / "src" / "launchd" / "gateway-bridge-wrapper.sh")
        def run(argv, **kw):
            calls.append(argv)
            if argv[:2] == ["launchctl", "print"]:
                return type("R", (), {"returncode": 0, "stdout": self._launchctl_print(wrapper, str(self.ws), 303)})()
            return type("R", (), {"returncode": 0, "stdout": ""})()
        with patch.object(mod.sys, "platform", "darwin"):
            out = mod.stop_running_bridge({}, run=run, kill=lambda pid, sig: kills.append(pid))
        self.assertEqual(out, {"mode": "launchd", "old_pid": 303})
        self.assertEqual([c[:2] for c in calls], [["launchctl", "print"], ["launchctl", "kickstart"]])
        self.assertEqual(kills, [], "the supervised path must never signal a pid — the replacement could be it")

    def test_macos_foreign_launchd_job_is_left_alone(self):
        calls = []
        def run(argv, **kw):
            calls.append(argv)
            return type("R", (), {"returncode": 0, "stdout": self._launchctl_print("/other/checkout/src/launchd/gateway-bridge-wrapper.sh", str(self.ws), 77)})()
        with patch.object(mod.sys, "platform", "darwin"):
            out = mod.stop_running_bridge({}, run=run, kill=lambda *a: (_ for _ in ()).throw(AssertionError("kill")))
        self.assertEqual(out["mode"], "none")
        self.assertFalse(any(c[:2] == ["launchctl", "kickstart"] for c in calls))

    # --- readiness -----------------------------------------------------------
    def _status(self, **kw):
        d = {"ts": time.time() + 1, "connected": True, "gateway": "https://relay.test/relay"}; d.update(kw)
        (self.ws / "state" / "gateway-status.json").write_text(json.dumps(d))

    def test_readiness_requires_a_new_lock_holder_not_the_old_pid(self):
        old = self._standin(); self._lock(old.pid); self._status()
        why = mod.wait_for_new_bridge({}, old.pid, time.time() - 5, "https://relay.test/relay", wait_s=1.5)
        self.assertIn("no new gateway bridge process", why)

    def test_readiness_rejects_same_host_different_path_and_other_host(self):
        new = self._standin(); self._lock(new.pid)
        for bad in ("https://relay.test/other-relay", "https://other-relay.test/relay", "https://relay.test.example/relay"):
            self._status(gateway=bad)
            why = mod.wait_for_new_bridge({}, None, time.time() - 5, "https://relay.test/relay", wait_s=1.5)
            self.assertIn("expected https://relay.test/relay", why, bad)

    def test_readiness_rejects_a_bridge_serving_another_agent(self):
        new = self._standin(); self._lock(new.pid); self._status()
        with patch.object(mod, "bridge_agent_id", return_value="@u-other.agent:x"):
            why = mod.wait_for_new_bridge({}, None, time.time() - 5, "https://relay.test/relay", "@u-gce.agent:x", wait_s=1.5)
        self.assertIn("serves @u-other.agent:x", why)

    def test_readiness_success_needs_new_pid_fresh_connected_status_exact_relay_and_agent(self):
        new = self._standin(); self._lock(new.pid); self._status(gateway="https://relay.test/relay/")
        with patch.object(mod, "bridge_agent_id", return_value="@u-gce.agent:x"):
            self.assertEqual(mod.wait_for_new_bridge({}, 1, time.time() - 5, "https://relay.test/relay", "@u-gce.agent:x", wait_s=3), "")
        self._status(connected=False, error="auth rejected HTTP 401")
        self.assertIn("not connected yet", mod.wait_for_new_bridge({}, 1, time.time() - 5, "https://relay.test/relay", wait_s=1.5))
        self._status(ts=time.time() - 100)
        self.assertIn("since the restart", mod.wait_for_new_bridge({}, 1, time.time() - 5, "https://relay.test/relay", wait_s=1.5))


if __name__ == "__main__":
    unittest.main(verbosity=2)
