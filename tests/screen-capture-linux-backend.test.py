#!/usr/bin/env python3
"""Linux backend of src/screen-capture-server.py.

The server was `screencapture`/`sips`/`system_profiler` throughout — on Linux
every capture raised FileNotFoundError and the voice agent's vision path went
dark. These tests load the module with sys.platform faked to "linux" and pin:
the capture argv per available tool, the ImageMagick downscale twin of sips,
the ffmpeg x11grab recording argv, and that the Darwin-only side effects
(notifyutil, CoreGraphics preflight) are skipped rather than attempted. The
module is also loaded as darwin to prove the macOS argv did not move.
"""
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "src" / "screen-capture-server.py"


def _load(platform: str):
    """Import the server fresh under a faked sys.platform (module constants derive from it)."""
    sys.modules.pop("screen_capture_server", None)
    with patch.object(sys, "platform", platform), \
            patch.dict(os.environ, {"SUTANDO_SCREENSHOT_DIR": "/nonexistent/shots"}):
        spec = importlib.util.spec_from_file_location("screen_capture_server", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


class LinuxBackend(unittest.TestCase):
    def setUp(self):
        self.m = _load("linux")

    def test_module_knows_it_is_not_darwin(self):
        self.assertFalse(self.m.IS_DARWIN)

    def test_capture_argv_per_tool(self):
        self.assertEqual(self.m.linux_capture_cmd("/t/a.png", "scrot"), ["scrot", "-o", "/t/a.png"])
        self.assertEqual(self.m.linux_capture_cmd("/t/a.png", "import"), ["import", "-window", "root", "/t/a.png"])
        self.assertEqual(self.m.linux_capture_cmd("/t/a.png", "gnome-screenshot"), ["gnome-screenshot", "-f", "/t/a.png"])

    def test_no_tool_means_none_not_a_guess(self):
        with patch.object(self.m.shutil, "which", return_value=None):
            self.assertIsNone(self.m.linux_screenshot_tool())
            self.assertIsNone(self.m.linux_capture_cmd("/t/a.png"))

    def test_tool_preference_order_matches_platform_sh(self):
        avail = {"import", "gnome-screenshot"}
        with patch.object(self.m.shutil, "which", side_effect=lambda t: t if t in avail else None):
            self.assertEqual(self.m.linux_screenshot_tool(), "import")
        avail.add("scrot")
        with patch.object(self.m.shutil, "which", side_effect=lambda t: t if t in avail else None):
            self.assertEqual(self.m.linux_screenshot_tool(), "scrot")

    def test_downscale_is_mogrify_with_bounded_resize(self):
        with patch.object(self.m.shutil, "which", return_value="/usr/bin/mogrify"):
            self.assertEqual(self.m.linux_downscale_cmd("/t/a.jpg", 1280, 60),
                             ["mogrify", "-resize", "1280x1280>", "-quality", "60", "/t/a.jpg"])
            self.assertEqual(self.m.linux_downscale_cmd("/t/a.png", None, None), ["mogrify", "/t/a.png"])
        with patch.object(self.m.shutil, "which", return_value=None):
            self.assertIsNone(self.m.linux_downscale_cmd("/t/a.jpg", 1280, 60))

    def test_downscale_without_tool_fails_closed_on_a_big_frame(self):
        with patch.object(self.m.shutil, "which", return_value=None), \
                patch.object(self.m.os.path, "getsize", return_value=self.m.DOWNSCALE_FAIL_MAX_BYTES + 1):
            self.assertFalse(self.m._downscale_frame("/t/a.jpg", 1280, 60))

    def test_record_argv_is_x11grab_until_sigint(self):
        with patch.dict(os.environ, {"DISPLAY": ":99"}), patch.object(self.m.shutil, "which", return_value=None):
            cmd = self.m.linux_record_cmd("/t/c.mp4", audio=True)
        self.assertEqual(cmd[:2], ["ffmpeg", "-loglevel"])
        self.assertIn("x11grab", cmd)
        self.assertEqual(cmd[cmd.index("-i") + 1], ":99")
        self.assertNotIn("pulse", cmd)          # no pactl → no audio input
        self.assertEqual(cmd[-1], "/t/c.mp4")
        with patch.object(self.m.shutil, "which", return_value="/usr/bin/pactl"):
            self.assertIn("pulse", self.m.linux_record_cmd("/t/c.mp4", audio=True, display=":1"))
            self.assertNotIn("pulse", self.m.linux_record_cmd("/t/c.mp4", audio=False, display=":1"))

    def test_list_displays_is_the_single_x_screen(self):
        with patch.dict(os.environ, {"DISPLAY": ":99"}), \
                patch.object(self.m, "linux_display_size", return_value=(1920, 1080)):
            self.assertEqual(self.m.list_displays(),
                             [{"index": 1, "width": 1920, "height": 1080, "name": ":99", "is_main": True}])

    def test_darwin_only_side_effects_are_skipped(self):
        with patch.object(self.m.subprocess, "Popen") as popen:
            self.m._post_recording_state(True)
        popen.assert_not_called()
        self.assertIsNone(self.m.screen_capture_permitted())


class DarwinUnchanged(unittest.TestCase):
    def test_darwin_argv_did_not_move(self):
        m = _load("darwin")
        self.assertTrue(m.IS_DARWIN)
        calls = []
        with patch.object(m.subprocess, "run", side_effect=lambda cmd, **kw: calls.append(cmd) or type("R", (), {"returncode": 0})()):
            m._downscale_frame("/t/a.jpg", 1280, 60)
        self.assertEqual(calls, [["sips", "--resampleHeightWidthMax", "1280", "-s", "format", "jpeg", "-s", "formatOptions", "60", "/t/a.jpg"]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
