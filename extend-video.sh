#!/usr/bin/env bash
#
# Start the Extend Video window.
#
#   ./extend-video.sh
#
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/tools/video-extend"
exec ./run.sh "$@"
