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

import collections
import fcntl
import json
import os
import queue
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
LOCK_PATH      = os.path.join(CONFIG_DIR, "timecap.lock")
LEGACY_CONFIG  = os.path.join(os.path.dirname(SOURCE_FILE), "config.json")

DEFAULT_CONFIG = {
    "capture_interval": 15,
    "idle_threshold":   300,
    "sound_enabled":    True,
    "base_dir":         os.path.expanduser("~/timecap"),
}


def volume_missing(path):
    """Return the unmounted /Volumes/<name> that `path` lives on, or None.

    Creating folders under a missing volume would either fail or quietly write
    to the boot disk — so every write path asks this first.
    """
    parts = path.split("/")
    if len(parts) > 2 and parts[1] == "Volumes" and parts[2]:
        volume = "/".join(parts[:3])
        if not os.path.ismount(volume):
            return volume
    return None


def log_event(event):
    """Append one line to <base_dir>/logs/tc_YYYYMMDD.log. Never raises.

    When the storage folder is unreachable (external drive unplugged), the line
    goes to ~/Library/Application Support/TimeCap/logs/ instead — so the outage
    itself is on record.
    """
    now = datetime.now()
    line = f"{now.strftime('%Y-%m-%d %H:%M:%S')} {event}\n"
    base = globals().get("BASE_DIR") or DEFAULT_CONFIG["base_dir"]
    for logs in (os.path.join(base, "logs"), os.path.join(CONFIG_DIR, "logs")):
        if volume_missing(logs):
            continue
        try:
            os.makedirs(logs, exist_ok=True)
            with open(os.path.join(logs, f"tc_{now.strftime('%Y%m%d')}.log"), "a") as f:
                f.write(line)
            return
        except Exception:
            continue


def migrate_legacy_config():
    """Copy a pre-existing source-adjacent config into CONFIG_PATH, once.
    Copy, not move — the old file may sit inside a signed bundle we cannot write to."""
    if os.path.exists(CONFIG_PATH) or not os.path.exists(LEGACY_CONFIG):
        return
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        shutil.copyfile(LEGACY_CONFIG, CONFIG_PATH)
    except Exception as e:
        log_event(f"CONFIG_MIGRATE_FAIL {e}")


def load_config():
    """Load config.json; fall back to DEFAULT_CONFIG if missing or broken."""
    migrate_legacy_config()
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
        except Exception as e:
            log_event(f"CONFIG_LOAD_FAIL {e}")
    return DEFAULT_CONFIG.copy()


def save_config():
    """Persist current module globals to config.json. App continues on failure."""
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump({
                "capture_interval": CAPTURE_INTERVAL,
                "idle_threshold":   IDLE_THRESHOLD,
                "sound_enabled":    SOUND_ENABLED,
                "base_dir":         BASE_DIR,
            }, f, indent=2)
    except Exception as e:
        log_event(f"CONFIG_SAVE_FAIL {e}")


def acquire_instance_lock():
    """Hold an exclusive lock for the life of the process.

    Returns the open lock file, or None when another TimeCap already holds it —
    two instances would capture into the same folder twice.
    """
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        handle = open(LOCK_PATH, "w")
    except OSError as e:
        log_event(f"LOCK_SKIPPED {e}")
        return True   # cannot check — do not block the app over it
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


_cfg = load_config()
CAPTURE_INTERVAL = _cfg["capture_interval"]   # seconds between captures (mutable via menu)
MIN_FRAMES       = 30                          # minimum frames required for video
FPS              = 4                           # output video framerate
IDLE_THRESHOLD   = _cfg["idle_threshold"]      # seconds — skip capture when user idle longer (mutable via menu)
SOUND_ENABLED    = _cfg["sound_enabled"]       # sound cue on pause/resume
BASE_DIR         = _cfg["base_dir"]            # storage folder — change via Storage menu

IDLE_POLL_INTERVAL = 1      # idle polling interval (s) — minimizes resume delay
UI_POLL_INTERVAL   = 0.2    # drain worker → UI callbacks on the main thread

INTERVAL_CHOICES  = [10, 15, 30, 60]             # capture interval choices (s)
IDLE_CHOICES_SEC  = [30, 60, 180, 300, 600]      # idle threshold choices (s) = 30s/1m/3m/5m/10m

PAUSE_SOUND  = "/System/Library/Sounds/Tink.aiff"
RESUME_SOUND = "/System/Library/Sounds/Pop.aiff"
_idle_probe_logged = False


def _fmt_seconds(sec):
    if sec < 60:
        return f"{sec}s"
    return f"{sec // 60}m"


def _last_lines(raw, count=3):
    """Last non-empty lines of a tool's stderr — where the actual error is."""
    text = raw.decode(errors="replace") if isinstance(raw, bytes) else (raw or "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " | ".join(lines[-count:])


def get_idle_seconds():
    """System idle time in seconds, from ioreg HIDIdleTime."""
    global _idle_probe_logged
    try:
        result = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem"],
            capture_output=True, text=True, timeout=2
        )
        for line in result.stdout.split("\n"):
            if "HIDIdleTime" in line:
                idle_ns = int(line.split("=")[-1].strip())
                return idle_ns / 1_000_000_000
    except Exception as e:
        if not _idle_probe_logged:
            _idle_probe_logged = True
            log_event(f"IDLE_PROBE_FAIL {e}")
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
        except Exception as e:
            log_event(f"SOUND_FAIL {e}")


def jpeg_size(path):
    """Return (width, height) read from a JPEG header, or None if unreadable."""
    try:
        with open(path, "rb") as f:
            if f.read(2) != b"\xff\xd8":
                return None
            while True:
                head = f.read(4)
                if len(head) < 4 or head[0] != 0xFF:
                    return None
                marker = head[1]
                length = int.from_bytes(head[2:4], "big")
                # SOF0–SOF15 carry the frame size (C4 / C8 / CC are other tables)
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    data = f.read(5)
                    if len(data) < 5:
                        return None
                    height = int.from_bytes(data[1:3], "big")
                    width  = int.from_bytes(data[3:5], "big")
                    return (width, height)
                f.seek(length - 2, 1)
    except OSError:
        return None


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
    def storage_problem():
        """Return None when the storage folder is usable, else a short reason.

        Never creates folders on a volume that is not mounted.
        """
        volume = volume_missing(BASE_DIR)
        if volume:
            return f"volume not mounted: {volume}"
        try:
            FileManager.ensure_dirs()
        except OSError as e:
            return f"cannot create folders: {e}"
        if not os.access(FileManager.screenshots_dir(), os.W_OK):
            return "folder not writable"
        return None

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
        """Return sorted list of jpg files currently in screenshots/.

        Empty when the folder cannot be read (e.g. external drive unplugged).
        """
        d = FileManager.screenshots_dir()
        try:
            names = os.listdir(d)
        except OSError:
            return []
        files = sorted([
            os.path.join(d, f)
            for f in names
            if f.startswith("tc_") and f.endswith(".jpg")
        ])
        return files

    @staticmethod
    def video_size(files):
        """Pick one output size for a video: the most common frame size, made even.

        Frames can differ in size when the display setup changed mid-recording.
        Returns None if no frame header could be read.
        """
        sizes = collections.Counter()
        for path in files:
            size = jpeg_size(path)
            if size:
                sizes[size] += 1
        if not sizes:
            return None
        width, height = sizes.most_common(1)[0][0]
        return (width - width % 2, height - height % 2)   # yuv420p needs even sides

    @staticmethod
    def video_archive_path(video_path, created_at):
        """Return archive/YYYY-MM/<video filename>/ for one created video."""
        month = created_at.strftime("%Y-%m")
        return os.path.join(
            FileManager.archive_dir(), month, os.path.basename(video_path)
        )

    @staticmethod
    def get_video_archive_dir(video_path, created_at):
        """Return the video archive path, creating it if needed."""
        path = FileManager.video_archive_path(video_path, created_at)
        os.makedirs(path, exist_ok=True)
        return path

    @staticmethod
    def move_to_archive(files, archive_path):
        """Move jpg files into one video-specific archive folder.

        Returns (moved, failed). A failed move leaves its original frame in
        screenshots/ so it can be retried manually. An existing file in the
        archive is never overwritten.
        """
        moved = 0
        failed = 0
        for filepath in files:
            fname = os.path.basename(filepath)
            dest = os.path.join(archive_path, fname)
            if os.path.exists(filepath):
                if os.path.exists(dest):
                    failed += 1
                    continue
                try:
                    shutil.move(filepath, dest)
                    moved += 1
                except OSError:
                    failed += 1
        return moved, failed

    @staticmethod
    def write_manifest(archive_path, video_path, files, created_at):
        """Write a small, human-readable description of one video archive."""
        names = [os.path.basename(path) for path in files]

        def frame_time(name):
            try:
                return datetime.strptime(name[3:18], "%Y%m%d_%H%M%S").strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            except ValueError:
                return name

        manifest_path = os.path.join(archive_path, "manifest.txt")
        with open(manifest_path, "w") as f:
            f.write(f"video: {os.path.basename(video_path)}\n")
            f.write(f"created: {created_at.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"frames: {len(files)}\n")
            f.write(f"first_frame: {frame_time(names[0])}\n")
            f.write(f"last_frame: {frame_time(names[-1])}\n")


# ─────────────────────────────────────────
# main app
# ─────────────────────────────────────────

class TimeCap(rumps.App):

    def __init__(self):
        super().__init__("TC")

        self.is_capturing  = False
        self.is_processing = False   # guard: Make Video / Archive Move
        self.is_idle       = False   # user is away (capture paused)
        self.storage_down  = FileManager.storage_problem() is not None
        self.frame_count   = len(FileManager.snapshot())
        self.fail_count    = 0
        self.skip_count    = 0       # frames skipped due to idle
        self._skip_logged  = False   # log idle-skip entry only once — avoids log flooding
        self.last_video_files = []   # frames left after latest video's auto archive
        self.last_video_archive = None  # destination for a manual retry
        self._ui_queue     = queue.Queue()   # worker thread → main thread callbacks
        self._worker       = None

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
        self._ui_timer    = rumps.Timer(self._drain_ui,       UI_POLL_INTERVAL)

        if self.storage_down:
            log_event(f"STORAGE_UNAVAILABLE at launch: {FileManager.storage_problem()}")
        self._sync_menu()

    # ── storage guard ────────────────────

    def _storage_blocked(self):
        """True (and tells the user) when the storage folder cannot be used right now."""
        problem = FileManager.storage_problem()
        if not problem:
            return False
        rumps.notification("TimeCap", "Storage unavailable", f"{BASE_DIR}\n{problem}")
        self.storage_down = True
        self._sync_menu()
        return True

    # ── Block 1 — Capture ────────────────

    def start_capture(self, _):
        if self.is_capturing:
            return
        if self._storage_blocked():
            return
        self.storage_down = False
        self.is_capturing = True
        self.is_idle      = False
        self.frame_count  = len(FileManager.snapshot())
        self._timer.start()
        self._idle_timer.start()
        log_event(f"CAPTURE_START interval={CAPTURE_INTERVAL}s")
        self._sync_menu()

    def stop_capture(self, _):
        if not self.is_capturing:
            return
        self.is_capturing = False
        self.is_idle      = False
        self._timer.stop()
        self._idle_timer.stop()
        log_event(f"CAPTURE_STOP frames={self.frame_count}")
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

    def capture_tick(self, _):
        # idle_check_tick maintains self.is_idle via 1 s polling — just trust it here.
        if self.is_idle:
            if not self._skip_logged:
                # entering idle-skip span — log once (per-tick logging would flood).
                log_event("CAPTURE_SKIP idle=True")
                self._skip_logged = True
            self.skip_count += 1
            self._sync_menu()
            return

        # Storage gone (external drive unplugged)? Wait quietly and resume when it
        # is back. Logged and notified once per outage, not once per tick.
        problem = FileManager.storage_problem()
        if problem:
            if not self.storage_down:
                self.storage_down = True
                log_event(f"STORAGE_UNAVAILABLE {problem}")
                rumps.notification(
                    "TimeCap", "Storage unavailable",
                    f"{BASE_DIR}\nCapture resumes when the folder is back."
                )
            self._sync_menu()
            return
        if self.storage_down:
            self.storage_down = False
            self.frame_count  = len(FileManager.snapshot())
            log_event("STORAGE_BACK")

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
            log_event("CAPTURE_TIMEOUT")
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
            log_event(f"CAPTURE_FAIL rc={result.returncode} {_last_lines(result.stderr, 1)}")
            self._sync_menu()
            if self.fail_count == 1:
                rumps.notification(
                    "TimeCap", "Capture failed",
                    "Check Screen Recording permission: System Settings → Privacy & Security → Screen Recording"
                )
                subprocess.Popen(
                    ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"]
                )

    # ── worker ↔ main thread ─────────────
    #
    # macOS UI is main-thread only. Workers do file work, then hand one callback
    # to _ui_queue; _drain_ui runs it on the main thread via a rumps.Timer.

    def _start_worker(self, target, *args):
        self.is_processing = True
        self._sync_menu()
        self._ui_timer.start()
        self._worker = threading.Thread(target=target, args=args, daemon=True)
        self._worker.start()

    def _drain_ui(self, _=None):
        while True:
            try:
                callback = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            callback()
        if not self.is_processing:
            self._ui_timer.stop()

    # ── Block 2 — Make Video ─────────────

    def make_video(self, _):
        if self.is_processing:
            rumps.notification("TimeCap", "Busy", "Another operation is in progress")
            return
        if self._storage_blocked():
            return

        files = FileManager.snapshot()

        if len(files) < MIN_FRAMES:
            needed = MIN_FRAMES - len(files)
            rumps.notification(
                "TimeCap",
                f"Need {needed} more frame{'s' if needed != 1 else ''}",
                f"{len(files)} captured — Make Video starts at {MIN_FRAMES}"
            )
            return

        cap_status = "capture running" if self.is_capturing else "capture stopped"
        rumps.notification(
            "TimeCap", "Make Video",
            f"{len(files)} frames / {cap_status}"
        )

        # `files` is a snapshot — captures made during/after Make Video are not
        # swept up by this video's archive.
        self._start_worker(self._run_ffmpeg, files)

    def _run_ffmpeg(self, files):
        """Worker thread. File work only — no UI calls here."""
        try:
            outcome = self._render_and_archive(files)
        except Exception as e:
            log_event(f"VIDEO_FAIL unexpected {e!r}")
            outcome = {"ok": False, "title": "Make Video failed", "error": str(e)}
        self._ui_queue.put(lambda: self._video_done(files, outcome))

    def _render_and_archive(self, files):
        if not FFMPEG:
            log_event("VIDEO_FAIL ffmpeg not found")
            return {"ok": False, "title": "ffmpeg not found",
                    "error": "Install via: brew install ffmpeg"}

        out  = FileManager.new_video_path()
        # Render under a temporary name: a half-written file never looks like a video.
        part = out + ".part"

        command = [FFMPEG, "-y", "-r", str(FPS), "-f", "concat", "-safe", "0"]
        size = FileManager.video_size(files)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix="_filelist.txt", delete=False
        ) as f:
            for fp in files:
                # escape single quotes for the ffmpeg concat list: ' → '\''
                escaped = fp.replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")
            list_path = f.name

        command += ["-i", list_path]
        if size:
            # Fit every frame into one size, keeping its aspect ratio (black bars
            # fill the rest) — frames from a different display are not stretched.
            w, h = size
            command += ["-vf", (
                f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1"
            )]
        command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-f", "mp4", part]

        try:
            result = subprocess.run(command, capture_output=True)
            video_ready = (
                result.returncode == 0
                and os.path.isfile(part)
                and os.path.getsize(part) > 0
            )
            if not video_ready:
                # Frames are untouched — nothing was moved.
                reason = _last_lines(result.stderr)
                log_event(f"VIDEO_FAIL rc={result.returncode} {reason}")
                return {"ok": False, "title": "ffmpeg error",
                        "error": _last_lines(result.stderr, 1)[:120]}
            os.replace(part, out)
        finally:
            for leftover in (list_path, part):
                if os.path.exists(leftover):
                    os.remove(leftover)

        # Archive only the snapshot passed to ffmpeg. Captures made while
        # the video was rendering stay in screenshots/ for the next video.
        created_at = datetime.now()
        archive_path = FileManager.video_archive_path(out, created_at)
        try:
            archive_path = FileManager.get_video_archive_dir(out, created_at)
            FileManager.write_manifest(archive_path, out, files, created_at)
            moved, failed = FileManager.move_to_archive(files, archive_path)
        except OSError as e:
            log_event(f"ARCHIVE_FAIL {e}")
            moved, failed = 0, len(files)
        log_event(
            f"VIDEO_OK {os.path.basename(out)} frames={len(files)} "
            f"archived={moved} kept={failed}"
        )
        return {"ok": True, "moved": moved, "failed": failed,
                "archive_path": archive_path}

    def _video_done(self, files, outcome):
        """Main thread. Apply the worker's result to state, menu and notifications."""
        if outcome["ok"]:
            remaining = [f for f in files if os.path.exists(f)]
            self.last_video_files = remaining
            self.last_video_archive = outcome["archive_path"] if remaining else None
            detail = f"Video created: 1 / Frames archived: {outcome['moved']}"
            if outcome["failed"]:
                detail += f" ({outcome['failed']} kept for manual retry)"
            rumps.notification("TimeCap", "Video ready", detail)
        else:
            rumps.notification("TimeCap", outcome["title"], outcome["error"])
            # Video generation failed — invalidate the snapshot so Archive Move
            # does not act on a stale file set.
            self.last_video_files = []
            self.last_video_archive = None
        self.is_processing = False
        self.frame_count = len(FileManager.snapshot())
        self._sync_menu()

    # ── Block 3 — Archive Move ───────────

    def archive_move(self, _):
        if self.is_processing:
            rumps.notification("TimeCap", "Busy", "Another operation is in progress")
            return
        if self._storage_blocked():
            return

        # Manual Archive Move is a retry tool. It only moves remaining frames from
        # the latest successful video, never newer captures without a video.
        if self.last_video_files and self.last_video_archive:
            files = [f for f in self.last_video_files if os.path.exists(f)]
            archive_path = self.last_video_archive
        else:
            files = []
            archive_path = None

        if not files:
            rumps.notification(
                "TimeCap", "Archive Move",
                "No remaining frames from the latest video"
            )
            return

        self._start_worker(self._run_archive_move, files, archive_path)

    def _run_archive_move(self, files, archive_path):
        """Worker thread. File work only — no UI calls here."""
        try:
            os.makedirs(archive_path, exist_ok=True)
            moved, failed = FileManager.move_to_archive(files, archive_path)
        except OSError as e:
            log_event(f"ARCHIVE_FAIL {e}")
            moved, failed = 0, len(files)
        log_event(f"ARCHIVE_MOVE archived={moved} failed={failed}")
        self._ui_queue.put(
            lambda: self._archive_done(files, archive_path, moved, failed)
        )

    def _archive_done(self, files, archive_path, moved, failed):
        """Main thread. Apply the worker's result to state, menu and notifications."""
        detail = f"Frames archived: {moved}"
        if failed:
            detail += f" / {failed} failed"
        rumps.notification(
            "TimeCap", "Archive Move done", detail
        )
        self.last_video_files = [f for f in files if os.path.exists(f)]
        self.last_video_archive = archive_path if self.last_video_files else None
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
                "· Write access to the selected storage folder"
            )
        )

    # ── option setters ───────────────────

    def _interval_title(self):
        return f"⏱ Capture Interval [{CAPTURE_INTERVAL}s]"

    def _idle_title(self):
        return f"⏸ Idle Threshold [{_fmt_seconds(IDLE_THRESHOLD)}]"

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
        old_path = BASE_DIR
        BASE_DIR = new_path
        if FileManager.storage_problem():
            BASE_DIR = old_path
            rumps.notification(
                "TimeCap", "Storage folder not changed",
                f"Cannot prepare folders in:\n{new_path}"
            )
            return
        self.storage_down = False
        # The retry list points into the old folder — it does not carry over.
        self.last_video_files = []
        self.last_video_archive = None
        self.frame_count = len(FileManager.snapshot())
        self.storage_item.title = self._storage_title()
        save_config()
        log_event(f"STORAGE_CHANGED from {old_path}")
        self._sync_menu()
        rumps.notification("TimeCap", "Storage folder changed", new_path)

    # ── menubar sync ─────────────────────

    def _sync_menu(self):
        err  = "! " if (self.fail_count > 0 or self.storage_down) else ""
        proc = "⏳" if self.is_processing else ""
        needed = max(0, MIN_FRAMES - self.frame_count)

        if self.is_processing:
            self.video_item.title = "Make Video (processing)"
        elif needed:
            self.video_item.title = f"Make Video ({needed} more frames)"
        else:
            self.video_item.title = "Make Video"

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
        if self.storage_down:
            self.status_item.title = "! Storage unavailable — check the storage folder"
        elif self.is_capturing and self.is_idle:
            self.status_item.title = f"⏸ Idle — Capture Paused ({self.frame_count} frames, skipped {self.skip_count})"
        elif err or proc or self.is_capturing:
            self.status_item.title = label
        else:
            if needed:
                self.status_item.title = f"TC — {needed} more frames needed for video"
            else:
                self.status_item.title = "TC — ready for video"


# ─────────────────────────────────────────
# entry
# ─────────────────────────────────────────

if __name__ == "__main__":
    _instance_lock = acquire_instance_lock()   # held until the process exits
    if _instance_lock is None:
        rumps.alert(title="TimeCap", message="TimeCap is already running.")
    else:
        TimeCap().run()
