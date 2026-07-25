# TimeCap

A macOS menu bar app that captures your screen at a fixed interval and turns the frames into a timelapse video. Built for people who want a visual history of their workday — locally, on their own disk.

<p align="center"><img src="icon_source.png" width="128" alt="TimeCap icon"></p>

## Naked Code

TimeCap follows a "naked code" philosophy — the entire app is one readable Python file you can audit yourself (or hand to an AI):

- captures the screen via macOS's own `screencapture`
- saves everything to a folder **you** choose
- **no network calls, no external data transfer** — verify in [`TimeCap.py`](TimeCap.py)

## Features

- ⏱ Configurable capture interval (10 / 15 / 30 / 60 s)
- ⏸ Idle detection — pauses capture when you're away, resumes with a sound cue
- 🎬 Make Video — assembles captured frames into an mp4 timelapse (ffmpeg)
- 🗂 Archive Move — moves used frames into monthly `archive/YYYYMM/` folders
- 📁 Storage picker — keep captures on any folder / external drive

## Requirements

- macOS (tested on macOS 26)
- [ffmpeg](https://ffmpeg.org) for video generation: `brew install ffmpeg`
- To run from source: Python 3 with [`rumps`](https://github.com/jaredks/rumps) (`pip install rumps`)

## Run

From source:

```bash
python3 TimeCap.py
```

Or build a standalone app with py2app:

```bash
pip install py2app
python3 setup.py py2app        # → dist/TimeCap.app
```

## Permissions

macOS will require:

- **Screen & System Audio Recording** — for `screencapture`. Grant it in System Settings → Privacy & Security, then relaunch the app. Until granted, captures fail (the app notifies you and opens the settings pane).
- **Notifications** — for status alerts.

## Storage note

Frames are full-resolution JPEGs (~0.5 MB each on a Retina display). At the default 15 s interval that is roughly **120 MB per hour** — point the storage folder at a disk with room, and use Archive Move / delete old months regularly.

## Configuration

Settings live in `~/Library/Application Support/TimeCap/config.json` (created on first change): capture interval, idle threshold, sound on/off, storage folder. Everything is editable from the menu bar — no need to touch the file.

If you ran an older version that kept `config.json` next to `TimeCap.py`, it is copied over automatically the first time you launch the new one.

## License

[MIT](LICENSE)
