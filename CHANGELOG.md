# Changelog

## 1.2.2 — 2026-11-14

A stabilization release: no new features, existing ones made dependable.

### Fixed

- **`main` did not launch.** A cleanup commit after 1.2.1 left a reference to a removed constant, so the app stopped with a `NameError` on start. Anyone who cloned `main` between 2026-10-09 and this release was affected; 1.2.1 and earlier were not.
- **Stretched videos.** When the display setup changed mid-recording, every frame was squeezed into the size of the first one. The video now uses the most common frame size and fits the others into it with their aspect ratio intact (black bars fill the rest).
- **Useless ffmpeg error message.** A failed render showed ffmpeg's version banner. It now shows — and logs — the actual error line.
- **UI calls from worker threads.** Make Video and Archive Move touched the menu bar and notifications from a background thread. Workers now do file work only and hand the result to the main thread.
- The `!` failure marker stayed in the menu bar after Stop Capture.

### Storage folder on an external drive

- TimeCap no longer quits at launch when the storage folder is unreachable, and never creates folders on a volume that is not mounted.
- If the drive disappears while capturing, capture waits and resumes by itself when the folder is back. One notification and one log line per outage.
- While the folder is away, log lines go to `~/Library/Application Support/TimeCap/logs/`.
- The failure notification no longer points at Screen Recording permission when the real cause is missing storage.

### Safer file handling

- Videos are rendered to a `.part` file and renamed only after ffmpeg succeeds — a half-written file never looks like a finished video.
- A frame already present in the archive is never overwritten; the original stays in `screenshots/` instead.

### Logging

- The daily log now also records capture start/stop, videos made, frames archived and storage outages.
- Capture failures carry their reason, and are logged once per failure streak (closed by `CAPTURE_OK after N failed`) instead of once per tick.

### Other

- Only one TimeCap can run at a time (lock file in `~/Library/Application Support/TimeCap/`).
- Removed the Idle Mode menu. Its `compress` and `label` entries were never implemented; idle behaviour is unchanged (frames are skipped while you are away).
- Added `tests/test_timecap.py` — standard library only, safe to run next to a live TimeCap.

## 1.2.1 — 2026-08-08

- Make Video now archives exactly the frames it used, into `archive/YYYY-MM/<video filename>/` with a `manifest.txt`. Archive Move became a retry tool for frames that could not be moved.
- The menu shows how many frames are still missing before Make Video is available.
- Settings moved to `~/Library/Application Support/TimeCap/config.json`; an older `config.json` next to `TimeCap.py` is copied over on first launch.
- Default capture interval changed from 10 s to 15 s.

## 1.2 — 2026-07-24

First public release.
