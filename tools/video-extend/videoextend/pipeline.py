"""Orchestration: source video in, extended video out.

Strategy, and why:

* The AI models available for free cap out at ~3.4s (I2V) or ~5.1s (VACE) per
  call, so a 10s extension is generated as a chain of shorter clips.
* Each clip is seeded with the LAST FRAME of what comes before it, so every
  join starts from the picture that precedes it. That is what keeps subjects,
  colour, lighting and framing continuous instead of producing a related-but-
  separate clip.
* Everything is normalised to the source's resolution and frame rate before
  joining, so the result plays as one video.

The original file is opened read-only and never written to.
"""

from __future__ import annotations

import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import media
from .errors import CancelledError, ProviderError, VideoExtendError
from .probe import VideoInfo, probe
from .providers import DEFAULT_PROVIDER, GenerationRequest, get_provider
from .providers.base import VideoProvider

# Describes continued motion. Deliberately about *continuation*, not new content:
# the use case is a website hero loop where continuity beats novelty.
DEFAULT_PROMPT = (
    "continue this exact shot seamlessly: same scene, same subjects, same colours, "
    "same lighting and atmosphere, same camera movement and framing, "
    "smooth natural continuation of the existing motion, cinematic, consistent style"
)

DEFAULT_NEGATIVE = (
    "new scene, scene change, cut, different subject, different location, "
    "different colours, different lighting, camera jump, teleport, flicker, "
    "morphing, warping, text, watermark, subtitles, logo, "
    "static frozen frame, still picture, worst quality, low quality, blurry"
)


class Cancellation:
    """Cooperative cancel flag, checked between stages and segments."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise CancelledError("Cancelled.")


@dataclass
class ExtendResult:
    output_path: str
    web_path: str | None
    source: VideoInfo
    result: VideoInfo
    segments_generated: int
    requested_seconds: float
    warnings: list[str] = field(default_factory=list)


def _progress(cb, message: str, fraction: float) -> None:
    if cb:
        cb(message, max(0.0, min(1.0, fraction)))


def extend_video(
    source_path: str | Path,
    *,
    extend_seconds: float = 10.0,
    output_path: str | Path | None = None,
    provider: VideoProvider | str | None = None,
    token: str | None = None,
    prompt: str | None = None,
    seed: int = 42,
    make_web_version: bool = True,
    keep_audio: bool = True,
    progress_cb=None,
    cancellation: Cancellation | None = None,
    workdir: str | Path | None = None,
) -> ExtendResult:
    """Extend `source_path` by roughly `extend_seconds` and return the result."""

    cancel = cancellation or Cancellation()
    warnings: list[str] = []

    if isinstance(provider, (str, type(None))):
        provider = get_provider(provider or DEFAULT_PROVIDER, token=token)
    pinfo = provider.info

    # --- 1-3. Check the video and read its properties ------------------------
    _progress(progress_cb, "Checking the video...", 0.02)
    src = probe(source_path)
    source_file = Path(src.path)

    if extend_seconds <= 0:
        raise VideoExtendError("Extension length must be greater than zero.")

    plan = provider.plan_segments(extend_seconds)
    if not plan:
        raise VideoExtendError("Nothing to generate.")

    # --- temporary workspace; never inside the repo --------------------------
    owns_tmp = workdir is None
    tmp = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="videoextend_"))
    tmp.mkdir(parents=True, exist_ok=True)
    succeeded = False

    try:
        gen_w, gen_h = provider.fit_resolution(src.width, src.height)
        if (gen_w, gen_h) != (src.width, src.height):
            warnings.append(
                f"The free model generates at up to {pinfo.max_pixels:,} pixels, so the "
                f"continuation is created at {gen_w}x{gen_h} and scaled back up to "
                f"{src.width}x{src.height}. Expect it to look slightly softer than the "
                f"original."
            )
        if abs(pinfo.native_fps - src.fps) > 0.5:
            warnings.append(
                f"The model generates at {pinfo.native_fps:.0f} fps; the continuation is "
                f"resampled to the source's {src.fps:.2f} fps."
            )

        # --- 4-8. Generate the continuation, chained ------------------------
        seed_image = tmp / "anchor_000.png"
        media.last_frame(source_file, seed_image, info=src)

        raw_segments: list[Path] = []
        for index, seconds in enumerate(plan, start=1):
            cancel.raise_if_cancelled()
            _progress(
                progress_cb,
                f"Generating continuation {index} of {len(plan)} "
                f"(cloud GPU, this can queue)...",
                0.05 + 0.70 * ((index - 1) / len(plan)),
            )

            raw = tmp / f"segment_{index:03d}.mp4"
            try:
                provider.generate(
                    GenerationRequest(
                        image_path=str(seed_image),
                        prompt=prompt or DEFAULT_PROMPT,
                        seconds=seconds,
                        width=gen_w,
                        height=gen_h,
                        # Vary per segment but stay deterministic overall.
                        seed=seed + index - 1,
                        negative_prompt=DEFAULT_NEGATIVE,
                    ),
                    raw,
                )
            except ProviderError:
                if raw_segments:
                    # Partial success is still useful: keep what we have.
                    warnings.append(
                        f"Only {len(raw_segments)} of {len(plan)} continuation clips were "
                        f"generated before the cloud service stopped (most likely the daily "
                        f"free GPU quota). The video below is shorter than you asked for."
                    )
                    break
                raise

            cancel.raise_if_cancelled()
            # Seed the next clip from this clip's final frame.
            if index < len(plan):
                seed_image = tmp / f"anchor_{index:03d}.png"
                media.last_frame(raw, seed_image)
            raw_segments.append(raw)

        if not raw_segments:
            raise ProviderError("No continuation was generated.")

        # --- 9. Join to the original ----------------------------------------
        _progress(progress_cb, "Joining the video...", 0.80)

        normalised = [media.normalize(source_file, tmp / "part_000.mp4",
                                      src.width, src.height, src.fps)]
        for i, seg in enumerate(raw_segments, start=1):
            cancel.raise_if_cancelled()
            normalised.append(
                media.normalize(seg, tmp / f"part_{i:03d}.mp4",
                                src.width, src.height, src.fps)
            )

        joined = media.concat(normalised, tmp / "joined.mp4")

        # --- audio ----------------------------------------------------------
        final_tmp = joined
        if src.has_audio and keep_audio:
            _progress(progress_cb, "Handling audio...", 0.86)
            joined_info = probe(joined)
            try:
                final_tmp = media.mux_audio(
                    joined, source_file, tmp / "joined_audio.mp4",
                    fade_out_from=src.duration,
                    total_duration=joined_info.duration,
                )
                warnings.append(
                    "The AI continuation has no sound, so the original audio fades out "
                    "over the last second before the join instead of cutting abruptly."
                )
            except VideoExtendError as exc:
                warnings.append(f"Audio could not be carried over ({exc}). Video is silent.")
                final_tmp = joined

        # --- 10. Verify ------------------------------------------------------
        _progress(progress_cb, "Checking the result...", 0.90)
        generated_seconds = sum(plan[:len(raw_segments)])
        expected = src.duration + generated_seconds
        out_info = media.verify(final_tmp, expect_seconds=expected, tolerance=2.0)

        # --- deliver ---------------------------------------------------------
        if output_path is None:
            output_path = source_file.with_name(f"{source_file.stem}_extended.mp4")
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.resolve() == source_file.resolve():
            raise VideoExtendError("Refusing to overwrite the original video.")
        shutil.copyfile(final_tmp, output_path)

        web_path = None
        if make_web_version:
            _progress(progress_cb, "Making the web version...", 0.95)
            web_path = output_path.with_name(f"{output_path.stem}_web.mp4")
            try:
                media.web_export(output_path, web_path)
                web_path = str(web_path)
            except VideoExtendError as exc:
                warnings.append(f"Web version could not be made ({exc}).")
                web_path = None

        _progress(progress_cb, "Done.", 1.0)
        succeeded = True
        return ExtendResult(
            output_path=str(output_path),
            web_path=web_path,
            source=src,
            result=out_info,
            segments_generated=len(raw_segments),
            requested_seconds=extend_seconds,
            warnings=warnings,
        )
    finally:
        # Clean up after success and after a user cancel. A failure leaves the
        # workspace behind so the partial clips can be inspected.
        if owns_tmp and (succeeded or cancel.cancelled):
            shutil.rmtree(tmp, ignore_errors=True)
