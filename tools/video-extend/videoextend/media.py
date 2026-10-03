"""FFmpeg operations: frame extraction, normalisation, joining, export.

All video work happens here so the provider layer only ever deals in
"image in, short clip out".
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .errors import InvalidVideoError, ToolMissingError, VideoExtendError
from .probe import VideoInfo, probe


def _ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise ToolMissingError("ffmpeg is not installed. Install FFmpeg and try again.")
    return path


def _run(args: list[str], what: str, timeout: int = 900) -> None:
    try:
        res = subprocess.run(args, capture_output=True, text=True,
                             timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise VideoExtendError(f"{what} timed out.") from exc
    if res.returncode != 0:
        tail = (res.stderr or "").strip().splitlines()
        raise VideoExtendError(f"{what} failed: {tail[-1] if tail else 'unknown error'}")


def even(n: int) -> int:
    """H.264 with yuv420p needs even dimensions."""
    return int(n) // 2 * 2


def extract_frame(video: str | Path, at_seconds: float, out_png: str | Path) -> Path:
    """Write a single frame to PNG. Used to seed the AI continuation."""
    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    # -ss after -i is slower but frame-accurate, which matters for the seam.
    _run([_ffmpeg(), "-y", "-v", "error", "-i", str(video),
          "-ss", f"{max(at_seconds, 0):.4f}", "-frames:v", "1",
          "-q:v", "2", str(out)],
         "Extracting a frame", timeout=180)
    if not out.exists() or out.stat().st_size == 0:
        raise VideoExtendError("Could not read a frame from the video.")
    return out


def last_frame(video: str | Path, out_png: str | Path, info: VideoInfo | None = None) -> Path:
    """Extract the final usable frame — the visual anchor for the continuation.

    Seeking to `duration - one frame` is unreliable: container duration and the
    last frame's timestamp disagree often enough to land past the end and write
    nothing. Instead decode a tail window with `-update 1`, which overwrites the
    same PNG for every frame and so leaves the genuine last one.
    """
    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    info = info or probe(video)

    attempts: list[list[str]] = []
    # Cheap: decode only the tail. Widen the window if the first try is too tight.
    for window in (0.5, 2.0):
        if info.duration > window:
            attempts.append([_ffmpeg(), "-y", "-v", "error",
                             "-sseof", f"-{window}", "-i", str(video),
                             "-update", "1", "-q:v", "2", str(out)])
    # Last resort: decode the whole clip and keep the final frame.
    attempts.append([_ffmpeg(), "-y", "-v", "error", "-i", str(video),
                     "-update", "1", "-q:v", "2", str(out)])

    for args in attempts:
        try:
            _run(args, "Extracting the last frame", timeout=300)
        except VideoExtendError:
            continue
        if out.exists() and out.stat().st_size > 0:
            return out

    raise VideoExtendError(
        "Could not read the final frame of the video. The file may be corrupt."
    )


def normalize(src: str | Path, out: str | Path, width: int, height: int,
              fps: float, *, drop_audio: bool = True) -> Path:
    """Re-encode a clip to exact size/fps so segments concatenate cleanly.

    Scales to cover the target box then centre-crops, preserving aspect ratio
    rather than stretching the picture.
    """
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    w, h = even(width), even(height)
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={w}:{h},fps={fps:.6f},format=yuv420p"
    )
    args = [_ffmpeg(), "-y", "-v", "error", "-i", str(src),
            "-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "17",
            "-pix_fmt", "yuv420p"]
    args += ["-an"] if drop_audio else ["-c:a", "aac", "-b:a", "192k"]
    args += [str(out)]
    _run(args, "Preparing a clip")
    return out


def concat(segments: list[str | Path], out: str | Path) -> Path:
    """Join pre-normalised clips without re-encoding (stream copy)."""
    segments = [Path(s) for s in segments]
    if not segments:
        raise VideoExtendError("Nothing to join.")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    listing = out.parent / f".{out.stem}_concat.txt"
    listing.write_text(
        "".join(f"file '{s.resolve().as_posix()}'\n" for s in segments), encoding="utf-8"
    )
    try:
        _run([_ffmpeg(), "-y", "-v", "error", "-f", "concat", "-safe", "0",
              "-i", str(listing), "-c", "copy", str(out)], "Joining the video")
    finally:
        listing.unlink(missing_ok=True)
    return out


def mux_audio(video: str | Path, audio_source: str | Path, out: str | Path,
              *, fade_out_from: float, total_duration: float) -> Path:
    """Carry the original audio onto the joined video.

    The AI continuation is silent, so the original track is faded out over the
    last second before the seam. That reads as an intentional settle rather than
    a hard cut to silence.
    """
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fade_start = max(fade_out_from - 1.0, 0.0)
    af = f"afade=t=out:st={fade_start:.3f}:d=1.0,apad"
    _run([_ffmpeg(), "-y", "-v", "error",
          "-i", str(video), "-i", str(audio_source),
          "-filter_complex", f"[1:a]{af}[a]",
          "-map", "0:v:0", "-map", "[a]",
          "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
          "-t", f"{total_duration:.3f}", "-shortest", str(out)],
         "Adding the audio")
    return out


def web_export(src: str | Path, out: str | Path, *, max_width: int = 1920,
               crf: int = 23) -> Path:
    """Browser-friendly MP4: H.264 baseline-ish, yuv420p, faststart."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = (f"scale='min({max_width},iw)':-2:flags=lanczos,format=yuv420p")
    _run([_ffmpeg(), "-y", "-v", "error", "-i", str(src),
          "-vf", vf, "-c:v", "libx264", "-profile:v", "high", "-level", "4.0",
          "-preset", "slow", "-crf", str(crf), "-pix_fmt", "yuv420p",
          "-movflags", "+faststart", "-c:a", "aac", "-b:a", "128k",
          str(out)], "Making the web version")
    return out


def verify(path: str | Path, *, expect_seconds: float | None = None,
           tolerance: float = 1.5) -> VideoInfo:
    """Confirm the finished file is a real, playable video of about the right length."""
    info = probe(path)  # Raises InvalidVideoError if unreadable.
    if expect_seconds is not None and abs(info.duration - expect_seconds) > tolerance:
        raise InvalidVideoError(
            f"Finished video is {info.duration:.1f}s but about "
            f"{expect_seconds:.1f}s was expected."
        )
    # Decode every frame to catch truncated or corrupt output.
    _run([_ffmpeg(), "-v", "error", "-i", str(path), "-f", "null", "-"],
         "Checking the finished video", timeout=600)
    return info


def resize_image(src: str | Path, out: str | Path, width: int, height: int) -> Path:
    """Resize a still to exact dimensions, preserving aspect by centre-cropping."""
    src, out = Path(src), Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # ffmpeg will not read and write the same path in one pass.
    tmp = out.with_name(f".{out.stem}_resize{out.suffix}")
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
          f"crop={width}:{height}")
    _run([_ffmpeg(), "-y", "-v", "error", "-i", str(src), "-vf", vf, str(tmp)],
         "Resizing the frame", timeout=180)
    tmp.replace(out)
    return out
