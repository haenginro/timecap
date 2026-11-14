#!/usr/bin/env python3
"""
Regression tests for TimeCap.py — standard library only.

    python3 tests/test_timecap.py

Safe to run next to a live TimeCap: HOME points at a temp folder, rumps is a
stub (no menu bar item appears), and screencapture is never really called.
The video tests use the real ffmpeg and are skipped when it is not installed.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SANDBOX = tempfile.mkdtemp(prefix="timecap_test_")
os.environ["HOME"] = os.path.join(SANDBOX, "home")
os.makedirs(os.environ["HOME"])

# ── rumps stub ───────────────────────────

NOTIFICATIONS = []   # (thread, title, subtitle, message)


class _MenuItem:
    def __init__(self, title, callback=None):
        self.title, self.callback, self.state = title, callback, 0

    def add(self, item):
        pass

    def set_callback(self, callback):
        self.callback = callback


class _Timer:
    def __init__(self, callback, interval):
        self.callback, self.interval, self.running = callback, interval, False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


class _App:
    def __init__(self, name):
        self.title, self.menu = name, []


def _notification(title, subtitle, message):
    NOTIFICATIONS.append((threading.current_thread(), title, subtitle, message))


rumps_stub = types.ModuleType("rumps")
rumps_stub.MenuItem, rumps_stub.Timer, rumps_stub.App = _MenuItem, _Timer, _App
rumps_stub.notification = _notification
rumps_stub.alert = lambda **kwargs: None
sys.modules["rumps"] = rumps_stub

spec = importlib.util.spec_from_file_location("TimeCap", os.path.join(REPO, "TimeCap.py"))
tc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tc)

FFMPEG = tc.FFMPEG
FFPROBE = shutil.which("ffprobe")
MISSING_VOLUME = "/Volumes/__timecap_test_not_mounted__"


def make_jpeg(path, width, height):
    subprocess.run(
        [FFMPEG, "-y", "-f", "lavfi", "-i", f"color=c=gray:s={width}x{height}",
         "-frames:v", "1", path],
        capture_output=True, check=True,
    )


def video_size(path):
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return tuple(int(n) for n in out.split(","))


def read_logs(folder):
    text = ""
    if os.path.isdir(folder):
        for name in sorted(os.listdir(folder)):
            with open(os.path.join(folder, name)) as f:
                text += f.read()
    return text


class Base(unittest.TestCase):

    def setUp(self):
        self.base = tempfile.mkdtemp(dir=SANDBOX)
        tc.BASE_DIR = self.base
        NOTIFICATIONS.clear()
        fallback = os.path.join(tc.CONFIG_DIR, "logs")
        if os.path.isdir(fallback):
            shutil.rmtree(fallback)

    def shots(self):
        return os.path.join(self.base, "screenshots")

    def fill_frames(self, sizes, count=30):
        """Create `count` frames, cycling through `sizes`. Returns the paths."""
        os.makedirs(self.shots(), exist_ok=True)
        masters = {}
        paths = []
        for i in range(count):
            size = sizes[i * len(sizes) // count]
            if size not in masters:
                masters[size] = os.path.join(SANDBOX, f"master_{size[0]}x{size[1]}.jpg")
                if not os.path.exists(masters[size]):
                    make_jpeg(masters[size], *size)
            path = os.path.join(self.shots(), f"tc_20260101_0000{i:02d}.jpg")
            shutil.copyfile(masters[size], path)
            paths.append(path)
        return paths

    def run_worker(self, app):
        """Let the worker finish, then run its callback as the main-thread timer would."""
        app._worker.join(timeout=120)
        self.assertFalse(app._worker.is_alive())
        app._drain_ui()


class Startup(Base):

    def test_app_initialises(self):
        # c73cd46 removed IDLE_MODE_CHOICES but still used it: NameError at launch.
        app = tc.TimeCap()
        self.assertFalse(app.storage_down)
        self.assertTrue(os.path.isdir(self.shots()))

    def test_single_instance_lock(self):
        first = tc.acquire_instance_lock()
        self.assertTrue(first)
        self.assertIsNone(tc.acquire_instance_lock())
        first.close()
        again = tc.acquire_instance_lock()
        self.assertTrue(again)
        again.close()


class MissingStorage(Base):

    def setUp(self):
        super().setUp()
        tc.BASE_DIR = os.path.join(MISSING_VOLUME, "tc_shot")

    def tearDown(self):
        self.assertFalse(os.path.exists(MISSING_VOLUME), "ghost folder was created")

    def test_launch_survives_and_reports(self):
        app = tc.TimeCap()
        self.assertTrue(app.storage_down)
        self.assertEqual(app.frame_count, 0)
        self.assertIn("Storage unavailable", app.status_item.title)
        self.assertIn("STORAGE_UNAVAILABLE", read_logs(os.path.join(tc.CONFIG_DIR, "logs")))

    def test_actions_refuse_instead_of_raising(self):
        app = tc.TimeCap()
        app.start_capture(None)
        self.assertFalse(app.is_capturing)
        app.make_video(None)
        app.archive_move(None)
        self.assertFalse(app.is_processing)
        self.assertEqual([n[2] for n in NOTIFICATIONS], ["Storage unavailable"] * 3)

    def test_capture_waits_then_resumes(self):
        tc.BASE_DIR = self.base
        app = tc.TimeCap()
        app.is_capturing = True

        tc.BASE_DIR = os.path.join(MISSING_VOLUME, "tc_shot")   # drive unplugged
        with mock.patch.object(tc.subprocess, "run") as run:
            app.capture_tick(None)
            app.capture_tick(None)
            run.assert_not_called()                # no screencapture into nowhere
        self.assertTrue(app.storage_down)
        self.assertEqual(len(NOTIFICATIONS), 1)    # once per outage, not per tick
        fallback = read_logs(os.path.join(tc.CONFIG_DIR, "logs"))
        self.assertEqual(fallback.count("STORAGE_UNAVAILABLE"), 1)

        tc.BASE_DIR = self.base                    # drive back
        ok = subprocess.CompletedProcess([], 0, b"", b"")
        with mock.patch.object(tc.subprocess, "run", return_value=ok):
            app.capture_tick(None)
        self.assertFalse(app.storage_down)
        self.assertEqual(app.frame_count, 1)
        self.assertIn("STORAGE_BACK", read_logs(os.path.join(self.base, "logs")))


class Capture(Base):

    def test_failure_is_logged_with_reason(self):
        app = tc.TimeCap()
        failed = subprocess.CompletedProcess([], 1, b"", b"could not create image from display\n")
        with mock.patch.object(tc.subprocess, "run", return_value=failed), \
             mock.patch.object(tc.subprocess, "Popen"):
            app.capture_tick(None)
            app.capture_tick(None)
            app.capture_tick(None)
        self.assertEqual(app.fail_count, 3)
        self.assertTrue(app.title.startswith("! "))
        logs = read_logs(os.path.join(self.base, "logs"))
        self.assertEqual(                           # once per streak, with the reason
            logs.count("CAPTURE_FAIL rc=1 could not create image from display"), 1
        )
        self.assertEqual(len(NOTIFICATIONS), 1)

        ok = subprocess.CompletedProcess([], 0, b"", b"")
        with mock.patch.object(tc.subprocess, "run", return_value=ok):
            app.capture_tick(None)
        self.assertEqual(app.fail_count, 0)
        self.assertIn("CAPTURE_OK after 3 failed", read_logs(os.path.join(self.base, "logs")))

    def test_stop_clears_failure_marker(self):
        app = tc.TimeCap()
        app.start_capture(None)
        failed = subprocess.CompletedProcess([], 1, b"", b"denied\n")
        with mock.patch.object(tc.subprocess, "run", return_value=failed), \
             mock.patch.object(tc.subprocess, "Popen"):
            app.capture_tick(None)
        self.assertTrue(app.title.startswith("! "))
        app.stop_capture(None)
        self.assertFalse(app.title.startswith("! "))

    def test_start_stop_are_logged(self):
        app = tc.TimeCap()
        app.start_capture(None)
        app.stop_capture(None)
        logs = read_logs(os.path.join(self.base, "logs"))
        self.assertIn("CAPTURE_START", logs)
        self.assertIn("CAPTURE_STOP", logs)


class Files(Base):

    def test_snapshot_of_missing_folder_is_empty(self):
        tc.BASE_DIR = os.path.join(self.base, "gone")
        self.assertEqual(tc.FileManager.snapshot(), [])

    def test_archive_never_overwrites(self):
        archive = os.path.join(self.base, "archive", "x")
        os.makedirs(archive)
        os.makedirs(self.shots())
        src = os.path.join(self.shots(), "tc_20260101_000000.jpg")
        dest = os.path.join(archive, "tc_20260101_000000.jpg")
        with open(src, "w") as f:
            f.write("new")
        with open(dest, "w") as f:
            f.write("already archived")
        self.assertEqual(tc.FileManager.move_to_archive([src], archive), (0, 1))
        self.assertTrue(os.path.exists(src))
        with open(dest) as f:
            self.assertEqual(f.read(), "already archived")

    def test_jpeg_size_rejects_non_jpeg(self):
        path = os.path.join(self.base, "not.jpg")
        with open(path, "w") as f:
            f.write("not a jpeg")
        self.assertIsNone(tc.jpeg_size(path))
        self.assertIsNone(tc.jpeg_size(os.path.join(self.base, "missing.jpg")))
        self.assertIsNone(tc.FileManager.video_size([path]))


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg / ffprobe not installed")
class Video(Base):

    def test_jpeg_size_and_majority(self):
        files = self.fill_frames([(320, 240), (642, 271)], count=30)
        self.assertEqual(tc.jpeg_size(files[0]), (320, 240))
        self.assertEqual(tc.jpeg_size(files[-1]), (642, 271))
        # 10 small + 20 wide → the wide size wins, odd height rounded down
        self.assertEqual(tc.FileManager.video_size(files[5:]), (642, 270))

    def test_mixed_sizes_keep_aspect_and_archive(self):
        app = tc.TimeCap()
        files = self.fill_frames([(320, 240), (640, 270)], count=30)
        expected = tc.FileManager.video_size(files)
        app.make_video(None)
        self.assertTrue(app.is_processing)
        self.run_worker(app)

        self.assertFalse(app.is_processing)
        videos = os.listdir(os.path.join(self.base, "videos"))
        self.assertEqual(len(videos), 1)
        self.assertTrue(videos[0].endswith(".mp4"))        # no .part left behind
        video = os.path.join(self.base, "videos", videos[0])
        self.assertEqual(video_size(video), expected)       # first frame no longer decides

        self.assertEqual(os.listdir(self.shots()), [])      # every frame archived…
        month = os.listdir(os.path.join(self.base, "archive"))[0]
        archive = os.path.join(self.base, "archive", month, videos[0])
        self.assertEqual(len(os.listdir(archive)), 31)      # …30 frames + manifest
        self.assertIn("VIDEO_OK", read_logs(os.path.join(self.base, "logs")))

        main = threading.main_thread()
        self.assertTrue(NOTIFICATIONS)
        self.assertTrue(all(n[0] is main for n in NOTIFICATIONS))   # no UI from worker
        self.assertEqual(NOTIFICATIONS[-1][2], "Video ready")

    def test_failed_render_keeps_frames(self):
        app = tc.TimeCap()
        os.makedirs(self.shots(), exist_ok=True)
        files = []
        for i in range(30):
            path = os.path.join(self.shots(), f"tc_20260101_0000{i:02d}.jpg")
            with open(path, "w") as f:
                f.write("not a jpeg")
            files.append(path)
        app.make_video(None)
        self.run_worker(app)

        self.assertFalse(app.is_processing)
        self.assertTrue(all(os.path.exists(f) for f in files))          # nothing moved
        self.assertEqual(os.listdir(os.path.join(self.base, "videos")), [])   # no broken mp4
        self.assertEqual(os.listdir(os.path.join(self.base, "archive")), [])
        self.assertEqual(app.last_video_files, [])
        self.assertIn("VIDEO_FAIL", read_logs(os.path.join(self.base, "logs")))
        title, message = NOTIFICATIONS[-1][2], NOTIFICATIONS[-1][3]
        self.assertEqual(title, "ffmpeg error")
        self.assertNotIn("ffmpeg version", message)        # the real error, not the banner
        self.assertTrue(all(n[0] is threading.main_thread() for n in NOTIFICATIONS))

    def test_frames_captured_during_render_stay(self):
        app = tc.TimeCap()
        files = self.fill_frames([(320, 240)], count=30)
        late = os.path.join(self.shots(), "tc_20260101_000100.jpg")
        outcome_files = list(files)
        shutil.copyfile(files[0], late)            # arrives after the snapshot was taken
        outcome = app._render_and_archive(outcome_files)
        self.assertTrue(outcome["ok"])
        self.assertEqual(os.listdir(self.shots()), ["tc_20260101_000100.jpg"])


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(SANDBOX, ignore_errors=True)
