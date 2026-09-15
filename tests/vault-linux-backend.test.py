#!/usr/bin/env python3
"""Linux backend of src/vault_intercept.py.

Every vault operation shelled out to macOS `security`; on Linux that is
FileNotFoundError from inside the bridges' `vault set` interception. Now:
darwin keeps `security` untouched, linux uses libsecret's `secret-tool` with
the value on STDIN (never argv), and a host without secret-tool refuses with a
message naming the fix instead of raising an opaque OSError.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
import vault_intercept as vi  # noqa: E402


class _Result:
    def __init__(self, rc=0, out=b"", err=b""):
        self.returncode, self.stdout, self.stderr = rc, out, err


class LinuxSecretTool(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.environ.get("TMPDIR", "/tmp")) / f"vault-test-{os.getpid()}"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.patches = [
            patch.object(vi.sys, "platform", "linux"),
            patch.object(vi.shutil, "which", side_effect=lambda t: "/usr/bin/secret-tool" if t == "secret-tool" else None),
            patch.object(vi, "_manifest_path", return_value=str(self.tmp / "keys.json")),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_store_uses_secret_tool_with_value_on_stdin(self):
        with patch.object(vi.subprocess, "run", return_value=_Result()) as run:
            vi.set_vault_key("MY_TOKEN", "s3cret")
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], "/usr/bin/secret-tool")
        self.assertEqual(argv[1], "store")
        self.assertIn("service", argv)
        self.assertNotIn("s3cret", argv)
        self.assertEqual(run.call_args.kwargs["input"], b"s3cret")
        self.assertIn("MY_TOKEN", vi.list_vault_keys())

    def test_get_uses_lookup_and_raises_keyerror_when_absent(self):
        with patch.object(vi.subprocess, "run", return_value=_Result(0, b"v\n")):
            self.assertEqual(vi.get_vault_key("K"), "v")
        with patch.object(vi.subprocess, "run", return_value=_Result(1, b"")):
            with self.assertRaises(KeyError):
                vi.get_vault_key("K")

    def test_delete_uses_clear_and_drops_manifest(self):
        with patch.object(vi.subprocess, "run", return_value=_Result()):
            vi.set_vault_key("GONE", "x")
            vi.delete_vault_key("GONE")
        self.assertNotIn("GONE", vi.list_vault_keys())

    def test_no_secret_tool_refuses_with_the_fix_named(self):
        with patch.object(vi.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "libsecret-tools"):
                vi.set_vault_key("K", "v")
            with self.assertRaises(KeyError):
                vi.get_vault_key("K")

    def test_interception_still_redacts_when_store_fails(self):
        with patch.object(vi.shutil, "which", return_value=None):
            res = vi.intercept_vault_commands("vault set API_KEY abc123xyz")
        self.assertNotIn("abc123xyz", res.text)
        self.assertIn("API_KEY", res.failed)


class DarwinUnchanged(unittest.TestCase):
    def test_darwin_still_calls_security(self):
        with patch.object(vi.sys, "platform", "darwin"), \
                patch.object(vi.subprocess, "run", return_value=_Result(0, b"v")) as run:
            self.assertEqual(vi.get_vault_key("K"), "v")
        self.assertEqual(run.call_args.args[0][:2], ["security", "find-generic-password"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
