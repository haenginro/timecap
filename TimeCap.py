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

CONFIG_PATH    = os.path.join(os.path.dirname(SOURCE_FILE), "config.json")
DEFAULT_CONFIG = {
    "capture_interval": 10,
    "idle_threshold":   300,
    "idle_mode":        "skip",
    "sound_enabled":    True,
    "base_dir":         os.path.expanduser("~/timecap"),
}


def load_config():
    """config.json 로드. 없거나 깨졌으면 DEFAULT_CONFIG."""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config():
    """현재 모듈 전역값을 config.json에 저장. 실패해도 앱은 계속."""
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
IDLE_MODE        = _cfg["idle_mode"]           # skip | compress | label  (compress/label: 미구현, skip과 동일)
SOUND_ENABLED    = _cfg["sound_enabled"]       # pause/resume 사운드 알림
BASE_DIR         = _cfg["base_dir"]            # 저장 폴더 — 메뉴 Storage에서 변경

IDLE_POLL_INTERVAL = 1      # idle 상태 폴링 주기(초) — 복귀 딜레이 최소화용

INTERVAL_CHOICES  = [10, 15, 30, 60]             # 캡처 간격 선택지(초)
IDLE_CHOICES_SEC  = [30, 60, 180, 300, 600]      # 자리비움 감지 선택지(초)  = 30초/1분/3분/5분/10분
IDLE_MODE_CHOICES = ["skip", "compress", "label"]

PAUSE_SOUND  = "/System/Library/Sounds/Tink.aiff"
RESUME_SOUND = "/System/Library/Sounds/Pop.aiff"


def _fmt_seconds(sec):
    if sec < 60:
        return f"{sec}s"
    return f"{sec // 60}m"


def get_idle_seconds():
    """macOS 시스템 유휴 시간(초). ioreg HIDIdleTime 기반."""
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
    """event: 'pause' | 'resume'. SOUND_ENABLED OFF이면 무동작. 비블로킹(Popen)."""
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
        self._skip_logged  = False   # idle 스킵 구간 진입 시 로그 1회만 — 폭증 방지
        self.last_video_files = []   # Make Video 시점 파일 스냅샷 — Archive Move race 방지용

        # menu items
        self.status_item  = rumps.MenuItem("TC — idle",      callback=None)
        self.start_item   = rumps.MenuItem("Start Capture",  callback=self.start_capture)
        self.stop_item    = rumps.MenuItem("Stop Capture",   callback=self.stop_capture)
        self.video_item   = rumps.MenuItem("Make Video",     callback=self.make_video)
        self.archive_item = rumps.MenuItem("Archive Move",   callback=self.archive_move)

        # option submenus (개발 단계 — 모든 옵션 메뉴에 노출, 확정 후 상수로 굳힘)
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
            # compress / label — 추후 구현. 지금은 선택 불가 처리(callback=None).
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
        """1초마다 idle 상태 전이만 감지 — 복귀 딜레이 최소화 + 사운드 트리거."""
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
        """logs/tc_YYYYMMDD.log 에 한 줄 기록. 쓰기 실패해도 앱은 계속."""
        stamp = datetime.now().strftime("%Y%m%d")
        log_path = os.path.join(FileManager.logs_dir(), f"tc_{stamp}.log")
        try:
            with open(log_path, "a") as f:
                f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {event}\n")
        except Exception:
            pass

    def capture_tick(self, _):
        # idle_check_tick이 self.is_idle을 1초 폴링으로 유지함 — 여기서는 결과만 신뢰.
        if self.is_idle:
            # IDLE_MODE: skip(기본) / compress / label
            # compress, label은 미구현 — 일단 skip과 동일 동작
            if not self._skip_logged:
                # idle 스킵 구간 진입 — 1회만 기록(매 tick 기록 시 로그 폭증).
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
            # 외장드라이브 I/O 스톨 등으로 screencapture가 15초 내 끝나지 않음.
            # 메인 루프 freeze 방지 — CAPTURE_FAIL과 동일 처리.
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
                    "TimeCap", "캡처 실패",
                    "화면 기록 권한을 확인해주세요: 시스템 설정 → 개인정보 보호 → 화면 기록"
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

        # 스냅샷 — Make Video 도중/이후 신규 캡처가 Archive Move에 휩쓸리지 않게 잠그는 기준.
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
                # ffmpeg concat 리스트의 작은따옴표 이스케이프: ' → '\''
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
                # 영상 생성 실패 — 스냅샷 무효화. Archive Move가 잘못된 기준으로 이동하지 않게.
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

        # 직전 Make Video 결과가 있으면 그 시점 스냅샷만 archive (영상 이후 신규 캡처 제외).
        # 없으면 fallback: screenshots/ 전체.
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
        # 스냅샷 소비 완료 — 같은 리스트가 두 번 적용되지 않게 초기화.
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
                "── 필요 권한 ──\n"
                "· Screen Recording — 화면 캡처용\n"
                "· Notifications — 완료 알림용\n"
                "· Disk Access — selected folder 저장용"
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
        """CAPTURE_INTERVAL 변경 시 안전한 재시작."""
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
             'POSIX path of (choose folder with prompt "TimeCap 저장 폴더 선택")'],
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
                "TimeCap", "쓰기 권한 없음",
                f"선택한 폴더에 저장할 수 없습니다:\n{new_path}"
            )
            return

        global BASE_DIR
        BASE_DIR = new_path
        FileManager.ensure_dirs()
        self.frame_count = len(FileManager.snapshot())
        self.storage_item.title = self._storage_title()
        save_config()
        self._sync_menu()
        rumps.notification("TimeCap", "저장 폴더 변경", new_path)

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
