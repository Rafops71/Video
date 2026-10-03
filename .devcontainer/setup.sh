#!/usr/bin/env bash
#
# Runs once, automatically, when the Codespace is created.
# Installs FFmpeg and prepares the Extend Video tool so the first launch is instant.
#
set -euo pipefail

echo "Installing FFmpeg..."
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg >/dev/null

echo "Preparing Extend Video..."
cd "$(dirname "${BASH_SOURCE[0]}")/../tools/video-extend"
python3 -m venv .venv
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt

echo ""
echo "============================================"
echo "  Ready."
echo ""
echo "  To extend a video, type this and press Enter:"
echo ""
echo "      ./extend-video.sh"
echo ""
echo "============================================"
