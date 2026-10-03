"""Test doubles and fixture builders.

The fake provider lets the whole pipeline be exercised without spending the
real daily GPU quota. It generates a genuinely different-looking clip so that
"did the join actually happen" is observable.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from videoextend.errors import ProviderError, QuotaExceededError
from videoextend.providers.base import GenerationRequest, ProviderInfo, VideoProvider


def make_test_video(path: str | Path, *, seconds: float = 4.0, width: int = 640,
                    height: int = 360, fps: float = 24.0, audio: bool = False,
                    color: str = "blue") -> Path:
    """Build a small synthetic MP4 with ffmpeg."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    args = ["ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i",
            f"testsrc=size={width}x{height}:rate={fps}:duration={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                 "-c:a", "aac", "-b:a", "128k", "-shortest"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(seconds), str(path)]
    subprocess.run(args, check=True, capture_output=True, timeout=180)
    return path


class FakeProvider(VideoProvider):
    """Offline stand-in for a cloud provider."""

    def __init__(self, token=None, *, max_segment_seconds: float = 3.375,
                 fail_after: int | None = None, quota_error: bool = False):
        self.token = token
        self.calls: list[GenerationRequest] = []
        self._max = max_segment_seconds
        self._fail_after = fail_after
        self._quota_error = quota_error

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            key="fake", name="Fake", backend="in-process",
            max_segment_seconds=self._max, native_fps=24.0,
            max_pixels=480 * 832, free=True, requires_token=False,
            notes="test double",
        )

    def generate(self, request: GenerationRequest, out_path):
        if self._fail_after is not None and len(self.calls) >= self._fail_after:
            raise (QuotaExceededError("quota exhausted") if self._quota_error
                   else ProviderError("backend exploded"))
        self.calls.append(request)
        w, h = self.fit_resolution(request.width, request.height)
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        # A clip that is visibly not the source, at the provider's native fps.
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", f"smptebars=size={w}x{h}:rate=24:duration={request.seconds}",
             "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-t", str(request.seconds), str(out)],
            check=True, capture_output=True, timeout=180,
        )
        return out
