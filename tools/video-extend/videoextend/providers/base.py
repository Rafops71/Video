"""Provider abstraction.

A provider turns one still image into one short video clip that begins at that
image. Everything else — probing, chaining, joining, audio, export — lives in
the pipeline, so swapping cloud backends means writing one small class.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GenerationRequest:
    image_path: str          # Still frame the clip must start from.
    prompt: str              # Describes continued motion, not new content.
    seconds: float           # Desired clip length; provider clamps to its own max.
    width: int
    height: int
    seed: int = 42
    negative_prompt: str | None = None


@dataclass(frozen=True)
class ProviderInfo:
    key: str
    name: str
    backend: str             # Where the GPU work actually happens.
    max_segment_seconds: float
    native_fps: float
    max_pixels: int          # Provider's pixel budget (width * height).
    free: bool
    requires_token: bool
    notes: str = ""


class VideoProvider(ABC):
    """Base class for cloud video-continuation backends."""

    @property
    @abstractmethod
    def info(self) -> ProviderInfo: ...

    @abstractmethod
    def generate(self, request: GenerationRequest, out_path: str | Path) -> Path:
        """Generate one clip starting from `request.image_path`.

        Must return a path to a real video file, or raise a ProviderError
        subclass. Must not return a still image or a silent black clip.
        """

    def preflight(self) -> tuple[bool, str]:
        """Cheap reachability check. Returns (ok, human-readable message)."""
        return True, "No preflight check implemented for this provider."

    def fit_resolution(self, width: int, height: int) -> tuple[int, int]:
        """Shrink the request to the provider's pixel budget, keeping aspect ratio
        and snapping to a multiple of 32 (what these diffusion models expect).
        """
        budget = self.info.max_pixels
        w, h = max(int(width), 1), max(int(height), 1)
        if w * h > budget:
            scale = (budget / (w * h)) ** 0.5
            w, h = int(w * scale), int(h * scale)
        snap = lambda v: max(32, int(round(v / 32)) * 32)  # noqa: E731
        return snap(w), snap(h)

    def plan_segments(self, total_seconds: float) -> list[float]:
        """Split a requested extension into clips this provider can actually make."""
        cap = self.info.max_segment_seconds
        remaining = max(float(total_seconds), 0.0)
        out: list[float] = []
        while remaining > 0.05:
            take = min(cap, remaining)
            # Avoid a useless sliver at the end; fold it into the previous clip.
            if remaining - take < 0.5 and out:
                take = remaining
            out.append(round(take, 3))
            remaining -= take
        return out
