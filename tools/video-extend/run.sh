#!/usr/bin/env bash
#
# One command to start the Extend Video window.
#
#   ./run.sh
#
# Creates a private Python environment on first run, installs the two packages
# it needs, then opens the interface in your browser. Nothing AI-related is
# downloaded or run on this computer - the GPU work happens in the cloud.
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/.venv"

if ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null; then
  echo "FFmpeg is required but was not found."
  echo "  macOS:    brew install ffmpeg"
  echo "  Windows:  winget install Gyan.FFmpeg"
  echo "  Linux:    sudo apt install ffmpeg"
  exit 1
fi

if [[ ! -d "$VENV" ]]; then
  echo "First run: setting up (this takes a minute)..."
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet -r "$HERE/requirements.txt"
  echo "Setup complete."
fi

cd "$HERE"
exec "$VENV/bin/python" -m videoextend.ui "$@"
