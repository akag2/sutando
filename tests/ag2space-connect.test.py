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
    def test_stop_running_bridge_kills_this_checkouts_bridge_and_waits(self):
        marker = str(REPO / "src" / "remote-gateway-bridge.py")
        # A stand-in whose argv carries this checkout's bridge path, like a real bare launch.
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", marker])
        try:
            time.sleep(0.3)
            self.assertIn(proc.pid, mod.bridge_pids())
            with patch.object(mod.sys, "platform", "linux"):
                victims = mod.stop_running_bridge({}, wait_s=5)
            self.assertIn(proc.pid, victims)
            self.assertIsNotNone(proc.wait(timeout=5))
            self.assertNotIn(proc.pid, mod.bridge_pids())
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_wait_for_new_bridge_reports_why_when_nothing_starts(self):
        with patch.object(mod, "gateway_status_path", return_value=Path(tempfile.mkdtemp()) / "gateway-status.json"), \
                patch.object(mod, "bridge_pids", return_value=[]):
            why = mod.wait_for_new_bridge({}, [], time.time(), "https://relay.test/relay", wait_s=1)
        self.assertIn("no new gateway bridge process", why)

    def test_wait_for_new_bridge_accepts_a_fresh_connected_status_for_the_new_relay(self):
        d = Path(tempfile.mkdtemp()); status = d / "gateway-status.json"
        since = time.time()
        status.write_text(json.dumps({"ts": since + 1, "connected": True, "gateway": "https://relay.test/relay"}))
        with patch.object(mod, "gateway_status_path", return_value=status), \
                patch.object(mod, "bridge_pids", return_value=[4242]):
            self.assertEqual(mod.wait_for_new_bridge({}, [], since, "https://relay.test/relay", wait_s=2), "")
        # An old-relay status, even if connected, is not the new connection.
        status.write_text(json.dumps({"ts": since + 1, "connected": True, "gateway": "https://old.example/relay"}))
        with patch.object(mod, "gateway_status_path", return_value=status), \
                patch.object(mod, "bridge_pids", return_value=[4242]):
            self.assertIn("not reported connected", mod.wait_for_new_bridge({}, [], since, "https://relay.test/relay", wait_s=1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
