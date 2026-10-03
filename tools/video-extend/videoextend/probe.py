"""Read video metadata with ffprobe.

Deliberately subprocess-based: ffprobe is already present in this container and
avoids pulling a Python video library into the dependency set.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, asdict
from fractions import Fraction
from pathlib import Path

from .errors import InvalidVideoError, ToolMissingError

# Anything longer is almost certainly not a hero clip, and would burn the whole
# daily GPU quota on one job.
MAX_SOURCE_SECONDS = 120.0


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise ToolMissingError(
            f"{tool} is not installed. It ships with FFmpeg — install FFmpeg and try again."
        )
    return path


@dataclass(frozen=True)
class VideoInfo:
    path: str
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    video_codec: str
    audio_codec: str | None

    @property
    def orientation(self) -> str:
        if self.width > self.height:
            return "landscape"
        if self.height > self.width:
            return "portrait"
        return "square"

    @property
    def aspect_ratio(self) -> float:
        return self.width / self.height if self.height else 0.0

    def summary(self) -> str:
        audio = self.audio_codec if self.has_audio else "none"
        return (
            f"{self.duration:.1f}s · {self.width}x{self.height} "
            f"({self.orientation}) · {self.fps:.2f} fps · audio: {audio}"
        )

    def as_dict(self) -> dict:
        d = asdict(self)
        d["orientation"] = self.orientation
        d["aspect_ratio"] = round(self.aspect_ratio, 4)
        return d


def _parse_fps(rate: str | None) -> float:
    """Turn ffprobe's '24000/1001' style rate into a float."""
    if not rate or rate in ("0/0", "N/A"):
        return 0.0
    try:
        value = float(Fraction(rate))
    except (ValueError, ZeroDivisionError):
        return 0.0
    return value if value > 0 else 0.0


def probe(path: str | Path) -> VideoInfo:
    """Return metadata for `path`, or raise InvalidVideoError."""
    ffprobe = _require("ffprobe")
    p = Path(path)

    if not p.exists():
        raise InvalidVideoError(f"File not found: {p}")
    if not p.is_file():
        raise InvalidVideoError(f"Not a file: {p}")
    if p.stat().st_size == 0:
        raise InvalidVideoError(f"File is empty: {p.name}")

    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(p)],
            capture_output=True, text=True, timeout=120, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise InvalidVideoError(f"Timed out reading {p.name}.") from exc

    if out.returncode != 0:
        detail = (out.stderr or "").strip().splitlines()
        hint = detail[-1] if detail else "unreadable"
        raise InvalidVideoError(f"{p.name} is not a video file I can read ({hint}).")

    try:
        data = json.loads(out.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise InvalidVideoError(f"{p.name} produced unreadable metadata.") from exc

    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if video is None:
        raise InvalidVideoError(f"{p.name} has no video track.")

    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if width <= 0 or height <= 0:
        raise InvalidVideoError(f"{p.name} has no usable picture size.")

    # Rotation metadata means the displayed frame is transposed.
    rotation = 0
    for sd in video.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                rotation = int(abs(float(sd["rotation"]))) % 360
            except (TypeError, ValueError):
                rotation = 0
    if rotation in (90, 270):
        width, height = height, width

    fps = _parse_fps(video.get("avg_frame_rate")) or _parse_fps(video.get("r_frame_rate"))
    if fps <= 0:
        fps = 30.0  # Last resort; a sane default beats failing on odd containers.

    duration = 0.0
    for candidate in (video.get("duration"), (data.get("format") or {}).get("duration")):
        try:
            duration = float(candidate)
            if duration > 0:
                break
        except (TypeError, ValueError):
            continue

    if duration <= 0:
        raise InvalidVideoError(
            f"{p.name} has no readable duration — it may be corrupt or still being written."
        )
    if duration > MAX_SOURCE_SECONDS:
        raise InvalidVideoError(
            f"{p.name} is {duration:.0f}s long. This tool is built for short hero clips "
            f"(up to {MAX_SOURCE_SECONDS:.0f}s). Trim it first."
        )

    return VideoInfo(
        path=str(p.resolve()),
        duration=duration,
        width=width,
        height=height,
        fps=fps,
        has_audio=audio is not None,
        video_codec=str(video.get("codec_name") or "unknown"),
        audio_codec=str(audio.get("codec_name")) if audio else None,
    )
