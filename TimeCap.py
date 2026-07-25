#!/usr/bin/env python3
"""
TimeCap.py — Naked Code
macOS screen timelapse capture & video generator

· captures screen via screencapture (interval configurable in menu)
· saves to user-selected folder (default: ~/timecap/)
· no network calls
· no external data transfer
· source: View Source → verify yourself or ask AI
"""

import json
import os
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime

import rumps

# ─────────────────────────────────────────
# paths & config
# ─────────────────────────────────────────

SOURCE_FILE      = os.path.abspath(__file__)


def _find_ffmpeg():
    found = shutil.which("ffmpeg")
    if found:
        return found
    for path in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if os.path.isfile(path):
            return path
    return None


FFMPEG = _find_ffmpeg()

# Settings live outside the app, in the standard macOS location. Older versions
# kept config.json next to TimeCap.py — which lands *inside* the .app bundle once
# packaged, so every settings change would break the code signature.
CONFIG_DIR     = os.path.expanduser("~/Library/Application Support/TimeCap")
CONFIG_PATH    = os.path.join(CONFIG_DIR, "config.json")
LEGACY_CONFIG  = os.path.join(os.path.dirname(SOURCE_FILE), "config.json")

DEFAULT_CONFIG = {
    "capture_interval": 15,
    "idle_threshold":   300,
    "idle_mode":        "skip",
    "sound_enabled":    True,
    "base_dir":         os.path.expanduser("~/timecap"),
}


def migrate_legacy_config():
    """Copy a pre-existing source-adjacent config into CONFIG_PATH, once.
    Copy, not move — the old file may sit inside a signed bundle we cannot write to."""
    if os.path.exists(CONFIG_PATH) or not os.path.exists(LEGACY_CONFIG):
        return
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        shutil.copyfile(LEGACY_CONFIG, CONFIG_PATH)
    except Exception:
        pass


def load_config():
    """Load config.json; fall back to DEFAULT_CONFIG if missing or broken."""
    migrate_legacy_config()
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config():
    """Persist current module globals to config.json. App continues on failure."""
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump({
                "capture_interval": CAPTURE_INTERVAL,
                "idle_threshold":   IDLE_THRESHOLD,
                "idle_mode":        IDLE_MODE,
                "sound_enabled":    SOUND_ENABLED,
                "base_dir":         BASE_DIR,
            }, f, indent=2)
    except Exception:
        pass


_cfg = load_config()
CAPTURE_INTERVAL = _cfg["capture_interval"]   # seconds between captures (mutable via menu)
MIN_FRAMES       = 30                          # minimum frames required for video
FPS              = 4                           # output video framerate
IDLE_THRESHOLD   = _cfg["idle_threshold"]      # seconds — pause capture when user idle longer (mutable via menu)
IDLE_MODE        = _cfg["idle_mode"]           # skip | compress | label  (compress/label: not implemented, same as skip)
SOUND_ENABLED    = _cfg["sound_enabled"]       # sound cue on pause/resume
BASE_DIR         = _cfg["base_dir"]            # storage folder — change via Storage menu

IDLE_POLL_INTERVAL = 1      # idle polling interval (s) — minimizes resume delay

INTERVAL_CHOICES  = [10, 15, 30, 60]             # capture interval choices (s)
IDLE_CHOICES_SEC  = [30, 60, 180, 300, 600]      # idle threshold choices (s) = 30s/1m/3m/5m/10m
IDLE_MODE_CHOICES = ["skip", "compress", "label"]

PAUSE_SOUND  = "/System/Library/Sounds/Tink.aiff"
RESUME_SOUND = "/System/Library/Sounds/Pop.aiff"


def _fmt_seconds(sec):
    if sec < 60:
        return f"{sec}s"
    return f"{sec // 60}m"


def get_idle_seconds():
    """System idle time in seconds, from ioreg HIDIdleTime."""
    try:
        result = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem"],
            capture_output=True, text=True, timeout=2
        )
        for line in result.stdout.split("\n"):
            if "HIDIdleTime" in line:
                idle_ns = int(line.split("=")[-1].strip())
                return idle_ns / 1_000_000_000
    except Exception:
        pass
    return 0


def play_sound(event: str):
    """event: 'pause' | 'resume'. No-op when SOUND_ENABLED is off. Non-blocking (Popen)."""
    if not SOUND_ENABLED:
        return
    path = PAUSE_SOUND if event == "pause" else RESUME_SOUND if event == "resume" else None
    if path and os.path.exists(path):
        try:
            subprocess.Popen(
                ["afplay", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass


# ─────────────────────────────────────────
# Block 0 — FileManager
# ─────────────────────────────────────────

class FileManager:

    @staticmethod
    def screenshots_dir(): return os.path.join(BASE_DIR, "screenshots")

    @staticmethod
    def videos_dir():      return os.path.join(BASE_DIR, "videos")

    @staticmethod
    def archive_dir():     return os.path.join(BASE_DIR, "archive")

    @staticmethod
    def logs_dir():        return os.path.join(BASE_DIR, "logs")

    @staticmethod
    def ensure_dirs():
        for d in [FileManager.screenshots_dir(), FileManager.videos_dir(),
                  FileManager.archive_dir(), FileManager.logs_dir()]:
            os.makedirs(d, exist_ok=True)

    @staticmethod
    def new_capture_path():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return os.path.join(FileManager.screenshots_dir(), f"tc_{stamp}.jpg")

    @staticmethod
    def new_video_path():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return os.path.join(FileManager.videos_dir(), f"tc_{stamp}_{FPS}fps.mp4")

    @staticmethod
    def snapshot():
        """Return sorted list of jpg files currently in screenshots/."""
        d = FileManager.screenshots_dir()
        files = sorted([
            os.path.join(d, f)
            for f in os.listdir(d)
            if f.startswith("tc_") and f.endswith(".jpg")
        ])
        return files

    @staticmethod
    def get_archive_dir(month_str):
        """Return archive/YYYYMM/ path, create if missing."""
        path = os.path.join(FileManager.archive_dir(), month_str)
        os.makedirs(path, exist_ok=True)
        return path

    @staticmethod
    def move_to_archive(files):
        """Move jpg files to archive/YYYYMM/ based on filename date."""
        moved = 0
        for filepath in files:
            fname = os.path.basename(filepath)
            # tc_YYYYMMDD_HHMMSS.jpg → YYYYMM
            try:
                month_str = fname[3:11][:6]   # "tc_20260404_..." → "202604"
            except IndexError:
                month_str = datetime.now().strftime("%Y%m")

            dest_dir = FileManager.get_archive_dir(month_str)
            dest = os.path.join(dest_dir, fname)
            if os.path.exists(filepath):
                shutil.move(filepath, dest)
                moved += 1
        return moved


# ─────────────────────────────────────────
# main app
# ─────────────────────────────────────────

class TimeCap(rumps.App):

    def __init__(self):
        super().__init__("TC")
        FileManager.ensure_dirs()

        self.is_capturing  = False
        self.is_processing = False   # guard: Make Video / Archive Move
        self.is_idle       = False   # user is away (capture paused)
        self.frame_count   = len(FileManager.snapshot())
        self.fail_count    = 0
        self.skip_count    = 0       # frames skipped due to idle
        self._skip_logged  = False   # log idle-skip entry only once — avoids log flooding
        self.last_video_files = []   # file snapshot at Make Video time — guards Archive Move race

        # menu items
        self.status_item  = rumps.MenuItem("TC — idle",      callback=None)
        self.start_item   = rumps.MenuItem("Start Capture",  callback=self.start_capture)
        self.stop_item    = rumps.MenuItem("Stop Capture",   callback=self.stop_capture)
        self.video_item   = rumps.MenuItem("Make Video",     callback=self.make_video)
        self.archive_item = rumps.MenuItem("Archive Move",   callback=self.archive_move)

        # option submenus (dev stage — all options exposed in menu; freeze into constants once settled)
        self.interval_items = {}   # {seconds: MenuItem}
        self.interval_menu  = rumps.MenuItem(self._interval_title())
        for sec in INTERVAL_CHOICES:
            item = rumps.MenuItem(
                f"{sec}s",
                callback=lambda s, v=sec: self.set_interval(v),
            )
            if sec == CAPTURE_INTERVAL:
                item.state = 1
            self.interval_menu.add(item)
            self.interval_items[sec] = item

        self.idle_items = {}
        self.idle_menu  = rumps.MenuItem(self._idle_title())
        for sec in IDLE_CHOICES_SEC:
            item = rumps.MenuItem(
                _fmt_seconds(sec),
                callback=lambda s, v=sec: self.set_idle_threshold(v),
            )
            if sec == IDLE_THRESHOLD:
                item.state = 1
            self.idle_menu.add(item)
            self.idle_items[sec] = item

        self.mode_items = {}
        self.mode_menu  = rumps.MenuItem(self._mode_title())
        for mode in IDLE_MODE_CHOICES:
            implemented = (mode == "skip")
            label = mode if implemented else f"{mode} (not implemented)"
            # compress / label — future work; disabled for now (callback=None).
            cb = (lambda s, v=mode: self.set_idle_mode(v)) if implemented else None
            item = rumps.MenuItem(label, callback=cb)
            if mode == IDLE_MODE:
                item.state = 1
            self.mode_menu.add(item)
            self.mode_items[mode] = item

        self.sound_item = rumps.MenuItem(
            self._sound_title(),
            callback=self.toggle_sound,
        )

        self.storage_item = rumps.MenuItem(
            self._storage_title(),
            callback=self.choose_storage_path,
        )

        self.menu = [
            self.status_item,
            None,
            self.interval_menu,
            self.idle_menu,
            self.mode_menu,
            self.sound_item,
            self.storage_item,
            None,
            self.start_item,
            self.stop_item,
            None,
            self.video_item,
            self.archive_item,
            None,
            rumps.MenuItem("Open Screenshots", callback=self.open_screenshots),
            rumps.MenuItem("Open Videos",      callback=self.open_videos),
            rumps.MenuItem("Open Archive",     callback=self.open_archive),
            None,
            rumps.MenuItem("View Source",        callback=self.view_source),
            rumps.MenuItem("What this app does", callback=self.what_this_does),
        ]

        # timers — created but not started
        self._timer       = rumps.Timer(self.capture_tick,    CAPTURE_INTERVAL)
        self._idle_timer  = rumps.Timer(self.idle_check_tick, IDLE_POLL_INTERVAL)

        self._sync_menu()

    # ── Block 1 — Capture ────────────────

    def start_capture(self, _):
        if self.is_capturing:
            return
        self.is_capturing = True
        self.is_idle      = False
        self.frame_count  = len(FileManager.snapshot())
        self._timer.start()
        self._idle_timer.start()
        self._sync_menu()

    def stop_capture(self, _):
        if not self.is_capturing:
            return
        self.is_capturing = False
        self.is_idle      = False
        self._timer.stop()
        self._idle_timer.stop()
        self._sync_menu()

    def idle_check_tick(self, _):
        """Detect idle-state transitions every second — minimal resume delay + sound trigger."""
        idle = get_idle_seconds()
        was_idle = self.is_idle
        self.is_idle = idle > IDLE_THRESHOLD
        if self.is_idle == was_idle:
            return
        if self.is_idle:
            play_sound("pause")
        else:
            play_sound("resume")
        self._sync_menu()

    def _log_event(self, event):
        """Append one line to logs/tc_YYYYMMDD.log. App continues on write failure."""
        stamp = datetime.now().strftime("%Y%m%d")
        log_path = os.path.join(FileManager.logs_dir(), f"tc_{stamp}.log")
        try:
            with open(log_path, "a") as f:
                f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {event}\n")
        except Exception:
            pass

    def capture_tick(self, _):
        # idle_check_tick maintains self.is_idle via 1 s polling — just trust it here.
        if self.is_idle:
            # IDLE_MODE: skip (default) / compress / label
            # compress and label are not implemented — behave like skip for now
            if not self._skip_logged:
                # entering idle-skip span — log once (per-tick logging would flood).
                self._log_event("CAPTURE_SKIP idle=True")
                self._skip_logged = True
            self.skip_count += 1
            self._sync_menu()
            return

        path = FileManager.new_capture_path()
        try:
            result = subprocess.run(
                ["screencapture", "-x", "-t", "jpg", path],
                capture_output=True, timeout=15
            )
        except subprocess.TimeoutExpired:
            # screencapture did not finish within 15 s (e.g. external-drive I/O stall).
            # Prevents main-loop freeze — handled the same as CAPTURE_FAIL.
            self._skip_logged = False
            self.fail_count += 1
            self._log_event("CAPTURE_TIMEOUT")
            self._sync_menu()
            return

        if result.returncode == 0:
            self._skip_logged = False
            self.frame_count += 1
            self.fail_count   = 0
            self._sync_menu()
        else:
            self._skip_logged = False
            self.fail_count += 1
            self._log_event("CAPTURE_FAIL")
            self._sync_menu()
            if self.fail_count == 1:
                rumps.notification(
                    "TimeCap", "Capture failed",
                    "Check Screen Recording permission: System Settings → Privacy & Security → Screen Recording"
                )
                subprocess.Popen(
                    ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"]
                )

    # ── Block 2 — Make Video ─────────────

    def make_video(self, _):
        if self.is_processing:
            rumps.notification("TimeCap", "Busy", "Another operation is in progress")
            return

        files = FileManager.snapshot()

        if len(files) < MIN_FRAMES:
            rumps.notification(
                "TimeCap",
                "Not enough frames",
                f"{len(files)} captured — need at least {MIN_FRAMES}"
            )
            return

        # Snapshot — locks the file set so captures made during/after Make Video
        # are not swept up by Archive Move.
        self.last_video_files = list(files)
        self.is_processing = True
        self._sync_menu()

        cap_status = "capture running" if self.is_capturing else "capture stopped"
        rumps.notification(
            "TimeCap", "Make Video",
            f"{len(files)} frames / {cap_status}"
        )

        thread = threading.Thread(
            target=self._run_ffmpeg, args=(files,), daemon=True
        )
        thread.start()

    def _run_ffmpeg(self, files):
        if not FFMPEG:
            rumps.notification(
                "TimeCap", "ffmpeg not found",
                "Install via: brew install ffmpeg"
            )
            self.is_processing = False
            self._sync_menu()
            return

        out = FileManager.new_video_path()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix="_filelist.txt", delete=False
        ) as f:
            for fp in files:
                # escape single quotes for the ffmpeg concat list: ' → '\''
                escaped = fp.replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")
            list_path = f.name

        try:
            result = subprocess.run([
                FFMPEG, "-y",
                "-r", str(FPS),
                "-f", "concat",
                "-safe", "0",
                "-i", list_path,
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                out
            ], capture_output=True)

            if result.returncode == 0:
                rumps.notification("TimeCap", "Video ready", os.path.basename(out))
            else:
                rumps.notification(
                    "TimeCap", "ffmpeg error", result.stderr.decode()[:120]
                )
                # Video generation failed — invalidate the snapshot so Archive Move
                # does not act on a stale file set.
                self.last_video_files = []
        finally:
            if os.path.exists(list_path):
                os.remove(list_path)
            self.is_processing = False
            self._sync_menu()

    # ── Block 3 — Archive Move ───────────

    def archive_move(self, _):
        if self.is_processing:
            rumps.notification("TimeCap", "Busy", "Another operation is in progress")
            return

        # If a Make Video snapshot exists, archive only that file set
        # (excludes captures made after the video). Otherwise fall back to
        # everything currently in screenshots/.
        if self.last_video_files:
            files = [f for f in self.last_video_files if os.path.exists(f)]
            scope = "last video snapshot"
        else:
            files = FileManager.snapshot()
            scope = "all current frames"

        if not files:
            rumps.notification("TimeCap", "Archive Move", "No files to archive")
            return

        self.is_processing = True
        thread = threading.Thread(
            target=self._run_archive_move, args=(files, scope), daemon=True
        )
        thread.start()

    def _run_archive_move(self, files, scope="all"):
        moved = FileManager.move_to_archive(files)
        rumps.notification(
            "TimeCap", "Archive Move done", f"{moved} files moved ({scope})"
        )
        # Snapshot consumed — reset so the same list is never applied twice.
        self.last_video_files = []
        self.is_processing = False
        self.frame_count = len(FileManager.snapshot())
        self._sync_menu()

    # ── folder shortcuts ─────────────────

    def open_screenshots(self, _):
        subprocess.run(["open", FileManager.screenshots_dir()])

    def open_videos(self, _):
        subprocess.run(["open", FileManager.videos_dir()])

    def open_archive(self, _):
        subprocess.run(["open", FileManager.archive_dir()])

    # ── Naked Code ───────────────────────

    def view_source(self, _):
        subprocess.run(["open", "-t", SOURCE_FILE])

    def what_this_does(self, _):
        rumps.alert(
            title="TimeCap — Naked Code",
            message=(
                "· captures screen via screencapture (interval configurable in menu)\n"
                "· saves to user-selected folder (default: ~/timecap/)\n"
                "· no network calls\n"
                "· no external data transfer\n\n"
                "View Source → verify yourself or ask AI\n\n"
                "── Required permissions ──\n"
                "· Screen Recording — for screen capture\n"
                "· Notifications — for status alerts\n"
                "· Disk Access — to save into the selected folder"
            )
        )

    # ── option setters ───────────────────

    def _interval_title(self):
        return f"⏱ Capture Interval [{CAPTURE_INTERVAL}s]"

    def _idle_title(self):
        return f"⏸ Idle Threshold [{_fmt_seconds(IDLE_THRESHOLD)}]"

    def _mode_title(self):
        return f"🎭 Idle Mode [{IDLE_MODE}]"

    def _sound_title(self):
        return "🔔 Sound [ON]" if SOUND_ENABLED else "🔕 Sound [OFF]"

    def _restart_capture_timer(self):
        """Safe timer restart when CAPTURE_INTERVAL changes."""
        was_running = self.is_capturing
        self._timer.stop()
        self._timer = rumps.Timer(self.capture_tick, CAPTURE_INTERVAL)
        if was_running:
            self._timer.start()

    def set_interval(self, seconds):
        global CAPTURE_INTERVAL
        CAPTURE_INTERVAL = seconds
        for sec, item in self.interval_items.items():
            item.state = 1 if sec == seconds else 0
        self.interval_menu.title = self._interval_title()
        self._restart_capture_timer()
        save_config()

    def set_idle_threshold(self, seconds):
        global IDLE_THRESHOLD
        IDLE_THRESHOLD = seconds
        for sec, item in self.idle_items.items():
            item.state = 1 if sec == seconds else 0
        self.idle_menu.title = self._idle_title()
        save_config()

    def set_idle_mode(self, mode):
        global IDLE_MODE
        IDLE_MODE = mode
        for m, item in self.mode_items.items():
            item.state = 1 if m == mode else 0
        self.mode_menu.title = self._mode_title()
        save_config()

    def toggle_sound(self, sender):
        global SOUND_ENABLED
        SOUND_ENABLED = not SOUND_ENABLED
        sender.title = self._sound_title()
        save_config()

    def _storage_title(self):
        display = BASE_DIR.replace(os.path.expanduser("~"), "~")
        if len(display) > 30:
            display = "..." + display[-27:]
        return f"📁 Storage [{display}]"

    def choose_storage_path(self, _):
        if self.is_processing:
            rumps.notification("TimeCap", "Busy", "Another operation is in progress")
            return

        result = subprocess.run(
            ["osascript", "-e",
             'POSIX path of (choose folder with prompt "Choose TimeCap storage folder")'],
            capture_output=True, text=True
        )
        new_path = result.stdout.strip().rstrip("/")
        if not new_path:
            return

        try:
            test = os.path.join(new_path, ".tc_write_test")
            with open(test, "w") as f:
                f.write("ok")
            os.remove(test)
        except Exception:
            rumps.notification(
                "TimeCap", "No write permission",
                f"Cannot save to the selected folder:\n{new_path}"
            )
            return

        global BASE_DIR
        BASE_DIR = new_path
        FileManager.ensure_dirs()
        self.frame_count = len(FileManager.snapshot())
        self.storage_item.title = self._storage_title()
        save_config()
        self._sync_menu()
        rumps.notification("TimeCap", "Storage folder changed", new_path)

    # ── menubar sync ─────────────────────

    def _sync_menu(self):
        err  = "! " if self.fail_count > 0 else ""
        proc = "⏳" if self.is_processing else ""
        if self.is_capturing:
            if self.is_idle:
                cap = f"⏸ TC {self.frame_count}"
            else:
                cap = f"● TC {self.frame_count}"
            self.start_item.set_callback(None)
            self.stop_item.set_callback(self.stop_capture)
        else:
            cap = "TC"
            self.start_item.set_callback(self.start_capture)
            self.stop_item.set_callback(None)

        label = f"{err}{proc}{cap} [{CAPTURE_INTERVAL}s]"
        self.title = label
        if self.is_capturing and self.is_idle:
            self.status_item.title = f"⏸ Idle — Capture Paused ({self.frame_count} frames, skipped {self.skip_count})"
        elif err or proc or self.is_capturing:
            self.status_item.title = label
        else:
            self.status_item.title = "TC — idle"


# ─────────────────────────────────────────
# entry
# ─────────────────────────────────────────

if __name__ == "__main__":
    TimeCap().run()
