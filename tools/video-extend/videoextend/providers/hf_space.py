"""Hugging Face Spaces (ZeroGPU) providers.

The GPU work happens on Hugging Face's shared ZeroGPU infrastructure, not on
this machine. We call public Gradio Spaces through their documented HTTP API.

Every constant below was read from the live Space source and its
/gradio_api/info endpoint, not assumed. See README.md § "Verified facts".
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..errors import ProviderError, QuotaExceededError
from .base import GenerationRequest, ProviderInfo, VideoProvider

# Phrases ZeroGPU / Gradio use when the daily GPU allowance is gone.
_QUOTA_MARKERS = (
    "gpu task aborted",
    "quota",
    "exceeded your",
    "gpu quota",
    "zerogpu",
    "you have exceeded",
)


def _classify(exc: Exception) -> ProviderError:
    # gradio_client's AppError carries the Space-side message on .message /
    # .title; str(exc) alone can be as uninformative as "RuntimeError".
    parts = [type(exc).__name__, str(exc)]
    for attr in ("message", "title"):
        value = getattr(exc, attr, None)
        if isinstance(value, str):
            parts.append(value)
    text = " ".join(parts).lower()

    if any(m in text for m in _QUOTA_MARKERS):
        return QuotaExceededError(
            "The free Hugging Face GPU quota is used up for now.\n"
            "A free account gets 5 minutes of GPU time per day (2 minutes without "
            "signing in), and it resets 24 hours after your first use.\n"
            "Wait for the reset, or sign in with a free account token to get the "
            "larger allowance."
        )
    if "exceeds the maximum" in text or "too large" in text:
        return ProviderError(f"The cloud Space rejected the input as too large: {exc}")
    return ProviderError(
        f"The cloud video Space did not complete the job.\n"
        f"Reason: {exc}\n"
        "Public Spaces can also be asleep, restarting, or busy — trying again in a "
        "few minutes often works."
    )


class HFSpaceProvider(VideoProvider):
    """Shared behaviour for calling a Gradio Space's /generate_video endpoint."""

    _info: ProviderInfo
    _space_id: str

    def __init__(self, token: str | None = None):
        # Token is optional: anonymous use works, with a smaller daily quota.
        self.token = token or os.environ.get("HF_TOKEN") or None

    @property
    def info(self) -> ProviderInfo:
        return self._info

    def _client(self):
        try:
            from gradio_client import Client
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise ProviderError(
                "The 'gradio_client' package is missing. Run the launcher script "
                "(run.sh) which installs it automatically."
            ) from exc

        # The token argument was renamed between gradio_client 1.x (hf_token)
        # and 2.x (token), so pick whichever this install actually accepts.
        import inspect
        params = inspect.signature(Client.__init__).parameters
        kwargs = {"verbose": False}
        if self.token:
            if "token" in params:
                kwargs["token"] = self.token
            elif "hf_token" in params:
                kwargs["hf_token"] = self.token

        try:
            return Client(self._space_id, **kwargs)
        except Exception as exc:
            raise _classify(exc) from exc

    def preflight(self) -> tuple[bool, str]:
        try:
            self._client()
        except ProviderError as exc:
            return False, str(exc)
        who = "signed in" if self.token else "anonymous (smaller daily quota)"
        return True, f"Connected to {self._space_id} — {who}."

    def _extract_video(self, result) -> str:
        """Pull the video path out of the Space's return value.

        The verified signature returns (video, seed) where video is
        dict(video=filepath, subtitles=...). Older Gradio versions hand back a
        bare path or tuple, so handle those too rather than crashing.
        """
        node = result[0] if isinstance(result, (list, tuple)) and result else result
        if isinstance(node, dict):
            node = node.get("video") or node.get("path") or node.get("url")
        if isinstance(node, (list, tuple)) and node:
            node = node[0]
        if not isinstance(node, str) or not node:
            raise ProviderError(
                f"The cloud Space returned something unexpected: {type(node).__name__}"
            )
        return node

    def generate(self, request: GenerationRequest, out_path: str | Path) -> Path:
        raise NotImplementedError


class WanI2VFastProvider(HFSpaceProvider):
    """Wan2.1 I2V 14B (480P) + CausVid LoRA, via multimodalart/wan2-1-fast.

    True image-to-video: the supplied still becomes frame 0, which is what makes
    the join continuous rather than a cut to a related-looking clip.
    """

    _space_id = "multimodalart/wan2-1-fast"
    _info = ProviderInfo(
        key="wan-i2v-fast",
        name="Wan2.1 I2V 14B (fast)",
        backend="Hugging Face Space 'multimodalart/wan2-1-fast' on ZeroGPU",
        # Verified in the Space source: MAX_FRAMES_MODEL = 81, FIXED_FPS = 24.
        max_segment_seconds=81 / 24,          # 3.375 s
        native_fps=24.0,
        max_pixels=480 * 832,                 # NEW_FORMULA_MAX_AREA
        free=True,
        requires_token=False,
        notes=(
            "Starts generation from the exact frame supplied, so the seam is "
            "continuous. Max 3.37s per call, so a 10s extension is chained."
        ),
    )

    def generate(self, request: GenerationRequest, out_path: str | Path) -> Path:
        from gradio_client import handle_file

        seconds = min(float(request.seconds), self.info.max_segment_seconds)
        width, height = self.fit_resolution(request.width, request.height)
        client = self._client()

        try:
            result = client.predict(
                input_image=handle_file(request.image_path),
                prompt=request.prompt,
                height=float(height),
                width=float(width),
                duration_seconds=float(seconds),
                guidance_scale=1.0,
                steps=4.0,              # CausVid LoRA: 4 steps keeps GPU cost at ~60-75s.
                seed=float(request.seed),
                randomize_seed=False,   # Reproducible, and lets chained seeds stay related.
                api_name="/generate_video",
            )
        except Exception as exc:
            raise _classify(exc) from exc

        produced = self._extract_video(result)
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(produced, out)
        except OSError as exc:
            raise ProviderError(f"Could not save the generated clip: {exc}") from exc
        if out.stat().st_size == 0:
            raise ProviderError("The cloud Space returned an empty video file.")
        return out


class WanVaceFastProvider(HFSpaceProvider):
    """Wan2.1 VACE, via linoyts/wan2-1-VACE-fast.

    Longer clips per call (5.06s vs 3.37s) so it uses less quota overall, but
    its 'Reference' mode treats the still as a style/subject reference rather
    than as frame 0, so the seam is less exact. Offered as an alternative.
    """

    _space_id = "linoyts/wan2-1-VACE-fast"
    _info = ProviderInfo(
        key="wan-vace-fast",
        name="Wan2.1 VACE (fast)",
        backend="Hugging Face Space 'linoyts/wan2-1-VACE-fast' on ZeroGPU",
        # Verified in the Space source: MAX_FRAMES_MODEL = 81, FIXED_FPS = 16.
        max_segment_seconds=81 / 16,          # 5.0625 s
        native_fps=16.0,
        max_pixels=480 * 832,
        free=True,
        requires_token=False,
        notes=(
            "Fewer calls for the same length, but 'Reference' mode does not pin "
            "frame 0 to the supplied still, so continuity is looser."
        ),
    )

    def generate(self, request: GenerationRequest, out_path: str | Path) -> Path:
        from gradio_client import handle_file

        seconds = min(float(request.seconds), self.info.max_segment_seconds)
        width, height = self.fit_resolution(request.width, request.height)
        client = self._client()

        try:
            result = client.predict(
                gallery_images=[{"image": handle_file(request.image_path)}],
                mode="Reference",
                prompt=request.prompt,
                height=float(height),
                width=float(width),
                duration_seconds=float(seconds),
                guidance_scale=1.0,
                steps=4.0,
                seed=float(request.seed),
                randomize_seed=False,
                remove_bg=False,
                api_name="/generate_video",
            )
        except Exception as exc:
            raise _classify(exc) from exc

        produced = self._extract_video(result)
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(produced, out)
        if out.stat().st_size == 0:
            raise ProviderError("The cloud Space returned an empty video file.")
        return out
