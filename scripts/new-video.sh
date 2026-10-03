#!/usr/bin/env bash
#
# Scaffold a new HyperFrames video project under projects/.
#
# Usage:
#   scripts/new-video.sh <name> [resolution] [example]
#
#   name        project folder name, e.g. launch-teaser
#   resolution  landscape (default) | portrait | square | landscape-4k | portrait-4k
#   example     blank (default) | swiss-grid | warm-grain
#
# Example:
#   scripts/new-video.sh launch-teaser portrait
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

NAME="${1:-}"
RESOLUTION="${2:-landscape}"
EXAMPLE="${3:-blank}"

if [[ -z "$NAME" ]]; then
  echo "usage: scripts/new-video.sh <name> [resolution] [example]" >&2
  exit 1
fi

TARGET="$REPO_ROOT/projects/$NAME"
if [[ -e "$TARGET" ]]; then
  echo "error: $TARGET already exists" >&2
  exit 1
fi

mkdir -p "$REPO_ROOT/projects"

# HYPERFRAMES_SKIP_SKILLS: the skills are already vendored in .agents/skills,
# so init does not need to re-check them against GitHub on every scaffold.
cd "$REPO_ROOT/projects"
HYPERFRAMES_SKIP_SKILLS=1 npx hyperframes init "$NAME" \
  --example "$EXAMPLE" \
  --resolution "$RESOLUTION" \
  --non-interactive

# Vendor GSAP into the project so renders never depend on a CDN reachable at
# render time. Compositions should load it with:
#   <script src="assets/vendor/gsap.min.js"></script>
mkdir -p "$TARGET/assets/vendor"
cp "$REPO_ROOT/vendor/gsap/gsap.min.js" "$TARGET/assets/vendor/gsap.min.js"

echo
echo "Created projects/$NAME (GSAP vendored to assets/vendor/gsap.min.js)"
echo
echo "Next:"
echo "  cd projects/$NAME"
echo "  npx hyperframes check     # lint + runtime + layout + motion + contrast"
echo "  npx hyperframes render    # write renders/<name>_<timestamp>.mp4"
