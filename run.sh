#!/bin/bash
cd "$(dirname "$0")"

# prefer the local venv (rumps lives there); fall back to system python3
if [ -x ".venv/bin/python" ]; then
    exec .venv/bin/python TimeCap.py
fi
exec python3 TimeCap.py
