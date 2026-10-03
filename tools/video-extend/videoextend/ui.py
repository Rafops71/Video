"""Simple local web interface.

Deliberately minimal: choose a video, choose how many seconds, press one button.
Technical controls (provider, seed, prompt) are tucked behind "Advanced" so the
normal path stays three clicks.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
import traceback
from pathlib import Path

# Finished videos go here rather than beside the upload. The upload lives in
# Gradio's private temp folder, which Gradio will not serve files back out of,
# and whose name can be hundreds of characters long.
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "outputs"


def _safe_stem(name: str, limit: int = 40) -> str:
    """Turn an arbitrary upload name into a short, filesystem-safe stem."""
    stem = Path(name).stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return (stem[:limit] or "video")

from .errors import CancelledError, VideoExtendError
from .pipeline import (
    Cancellation, assemble_extension, extend_video, starting_frame,
)
from .probe import probe
from .providers import DEFAULT_PROVIDER, available

_cancel_lock = threading.Lock()
_active: Cancellation | None = None


def _describe(video_path):
    if not video_path:
        return "No video selected yet."
    try:
        info = probe(video_path)
    except VideoExtendError as exc:
        return f"**That file cannot be used.**\n\n{exc}"
    return (
        f"**Video:** {Path(info.path).name}  \n"
        f"**Duration:** {info.duration:.1f} seconds  \n"
        f"**Resolution:** {info.width}x{info.height} ({info.orientation})  \n"
        f"**Frame rate:** {info.fps:.2f} fps  \n"
        f"**Audio:** {info.audio_codec if info.has_audio else 'none'}"
    )


def _run(video_path, seconds, provider_key, token, prompt, make_web, keep_audio,
         progress=None):
    """Returns (status_markdown, result_video, web_video, download_files)."""
    global _active

    if not video_path:
        return "Choose a video first.", None, None, None

    cancel = Cancellation()
    with _cancel_lock:
        _active = cancel

    def on_progress(message, fraction):
        if progress is not None:
            progress(fraction, desc=message)

    try:
        result = extend_video(
            video_path,
            extend_seconds=float(seconds),
            provider=provider_key or DEFAULT_PROVIDER,
            token=(token or os.environ.get("HF_TOKEN") or None),
            prompt=(prompt.strip() or None) if prompt else None,
            make_web_version=bool(make_web),
            keep_audio=bool(keep_audio),
            progress_cb=on_progress,
            cancellation=cancel,
            # A short, predictable name in a folder Gradio is allowed to serve.
            # Timestamped so a second run never overwrites the first.
            output_path=OUTPUT_DIR / (
                f"{_safe_stem(video_path)}_{time.strftime('%H%M%S')}_extended.mp4"
            ),
        )
    except CancelledError:
        return "Cancelled. The original video is untouched.", None, None, None
    except VideoExtendError as exc:
        return f"**Could not finish.**\n\n{exc}", None, None, None
    except Exception as exc:  # pragma: no cover - last-resort guard
        traceback.print_exc()
        return f"**Unexpected error.**\n\n`{exc}`", None, None, None
    finally:
        with _cancel_lock:
            _active = None

    lines = [
        "**Done.**  ",
        f"Original: {result.source.duration:.1f}s → "
        f"Result: {result.result.duration:.1f}s  ",
        f"Saved to: `{result.output_path}`  ",
    ]
    if result.web_path:
        lines.append(f"Web version: `{result.web_path}`  ")
    for w in result.warnings:
        lines.append(f"\n> {w}")

    downloads = [result.output_path] + ([result.web_path] if result.web_path else [])
    return "\n".join(lines), result.output_path, result.web_path, downloads


SPACE_URL = "https://huggingface.co/spaces/multimodalart/wan2-1-fast"


def _get_start_frame(video_path):
    """Assisted step 1: hand back the frame the continuation must start from."""
    if not video_path:
        return "Choose a video first.", None
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        png = OUTPUT_DIR / f"{_safe_stem(video_path)}_{time.strftime('%H%M%S')}_lastframe.png"
        starting_frame(video_path, png)
        info = probe(video_path)
    except VideoExtendError as exc:
        return f"**Could not read that video.**\n\n{exc}", None

    frame_dims = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(png)],
        capture_output=True, text=True,
    ).stdout.strip().replace(",", "x")

    return (
        f"**Starting frame ready.** Press and hold the picture below to save it.\n\n"
        f"Your clip is **{info.duration:.1f}s** at **{info.width}x{info.height}**. "
        f"The frame is deliberately given to you at **{frame_dims or 'model size'}**: "
        f"the free GPUs run out of memory on full-size stills and fail with "
        f"*\"ZeroGPU worker error\"*. Upload it as-is and **do not raise the "
        f"resolution** in the Space. Nothing is lost — the model works at this size "
        f"anyway, and joining scales it back up to yours.\n\n"
        f"Each generated clip can be up to about **3.3 seconds**, so roughly "
        f"**3 clips** gets you ~10 more seconds.",
        str(png),
    )


def _assemble(video_path, clips, make_web, keep_audio, progress=None):
    """Assisted step 2: join the generated clips onto the original."""
    global _active
    if not video_path:
        return "Choose your original video first.", None, None, None
    if not clips:
        return "Add at least one generated clip.", None, None, None

    paths = [c if isinstance(c, str) else getattr(c, "name", None) for c in clips]
    cancel = Cancellation()
    with _cancel_lock:
        _active = cancel

    def on_progress(message, fraction):
        if progress is not None:
            progress(fraction, desc=message)

    try:
        result = assemble_extension(
            video_path,
            paths,
            make_web_version=bool(make_web),
            keep_audio=bool(keep_audio),
            progress_cb=on_progress,
            cancellation=cancel,
            output_path=OUTPUT_DIR / (
                f"{_safe_stem(video_path)}_{time.strftime('%H%M%S')}_extended.mp4"
            ),
        )
    except CancelledError:
        return "Cancelled. Your original is untouched.", None, None, None
    except VideoExtendError as exc:
        return f"**Could not finish.**\n\n{exc}", None, None, None
    except Exception as exc:  # pragma: no cover
        traceback.print_exc()
        return f"**Unexpected error.**\n\n`{exc}`", None, None, None
    finally:
        with _cancel_lock:
            _active = None

    lines = [
        "**Done.**  ",
        f"Original: {result.source.duration:.1f}s → Result: {result.result.duration:.1f}s  ",
        f"Joined {result.segments_generated} clip(s).  ",
    ]
    for w in result.warnings:
        lines.append(f"\n> {w}")
    downloads = [result.output_path] + ([result.web_path] if result.web_path else [])
    return "\n".join(lines), result.output_path, result.web_path, downloads


def _cancel():
    with _cancel_lock:
        if _active:
            _active.cancel()
            return "Cancelling after the current step..."
    return "Nothing is running."


def build_app():
    import gradio as gr

    provider_choices = [(f"{p.name} — max {p.max_segment_seconds:.1f}s per call", p.key)
                        for p in available()]

    with gr.Blocks(title="Extend Video") as app:
        gr.Markdown(
            "# Extend Video\n"
            "Add an AI-generated continuation to an existing clip. "
            "The AI runs on free cloud GPUs — your computer only handles the joining."
        )

      # --- Assisted route: the one that works on the free tier -------------
        with gr.Tab("Guided (works on the free tier)"):
            gr.Markdown(
                "Hugging Face gives a **browser** session a usable free GPU allowance, "
                "but heavily throttles the same model when it is called from code. "
                "So here you generate in the browser — where the free quota actually "
                "works — and this page does the rest.\n\n"
                "**You will need to be signed in to Hugging Face in another tab.**"
            )
            with gr.Row():
                with gr.Column():
                    gr.Markdown("### Step 1 — get the starting frame")
                    a_video = gr.Video(label="Your original video", sources=["upload"])
                    a_details = gr.Markdown("No video selected yet.")
                    a_frame_btn = gr.Button("GET STARTING FRAME", variant="primary")
                    a_frame_msg = gr.Markdown()
                    # An Image rather than a File: it renders inline, and on a
                    # tablet press-and-hold saves it straight to Photos.
                    a_frame_file = gr.Image(
                        label="Starting frame — press and hold to save it",
                        type="filepath",
                        interactive=False,
                    )

                    gr.Markdown(
                        f"### Step 2 — generate, in your browser\n"
                        f"1. Open **[the video model]({SPACE_URL})** (sign in first).\n"
                        f"2. Upload the frame you just downloaded.\n"
                        f"3. Describe the continuation, e.g. *“continue this exact shot, "
                        f"same scene, same lighting, smooth natural motion”*.\n"
                        f"4. Set the length to its maximum and generate.\n"
                        f"5. Download the clip.\n"
                        f"6. **For a longer extension:** take the *last frame* of that "
                        f"clip and repeat, so each piece continues the one before."
                    )

                with gr.Column():
                    gr.Markdown("### Step 3 — join it all together")
                    a_clips = gr.File(
                        label="The generated clip(s), in order",
                        file_count="multiple",
                        file_types=["video"],
                    )
                    a_web = gr.Checkbox(True, label="Also make a web-friendly MP4")
                    a_audio = gr.Checkbox(True, label="Keep the original audio")
                    a_join = gr.Button("JOIN INTO FINAL VIDEO", variant="primary")
                    a_status = gr.Markdown("Ready.")
                    a_result = gr.Video(label="Result", interactive=False)
                    a_webv = gr.Video(label="Web version", interactive=False)
                    a_dl = gr.File(label="Download", file_count="multiple")

            a_video.change(_describe, inputs=a_video, outputs=a_details)
            a_frame_btn.click(_get_start_frame, inputs=a_video,
                              outputs=[a_frame_msg, a_frame_file])

            def _assemble_handler(v, c, w, k, progress=gr.Progress()):
                return _assemble(v, c, w, k, progress=progress)

            a_join.click(_assemble_handler,
                         inputs=[a_video, a_clips, a_web, a_audio],
                         outputs=[a_status, a_result, a_webv, a_dl])

      # --- Automatic route: kept, but honestly labelled --------------------
        with gr.Tab("Automatic (often blocked)"):
            gr.Markdown(
                "⚠️ This route calls the model directly from code. It is the "
                "convenient one, but Hugging Face throttles API access to its free "
                "GPUs far more than browser use, so it usually fails with "
                "*“quota exceeded”* even with a valid token and plenty of allowance "
                "left. Try it if you like; use the **Guided** tab if it fails."
            )

            with gr.Row():
                with gr.Column(scale=1):
                    video_in = gr.Video(label="Choose Video", sources=["upload"])
                    details = gr.Markdown("No video selected yet.")
                    seconds = gr.Slider(2, 20, value=10, step=1,
                                        label="Extension (seconds)")
                    with gr.Row():
                        go = gr.Button("EXTEND VIDEO", variant="primary", scale=3)
                        stop = gr.Button("Cancel", scale=1)

                    with gr.Accordion("Advanced (optional)", open=False):
                        provider = gr.Dropdown(
                            provider_choices, value=DEFAULT_PROVIDER, label="Cloud provider"
                        )
                        token = gr.Textbox(
                            label="Hugging Face token (optional)",
                            type="password",
                            placeholder="Leave empty to use the smaller anonymous quota",
                            info="A free account gets 5 min/day of GPU time vs 2 min "
                                 "anonymous. Get one at huggingface.co/settings/tokens",
                        )
                        prompt = gr.Textbox(
                            label="Describe the continuation (optional)",
                            placeholder="Leave empty to simply continue the existing shot",
                            lines=2,
                        )
                        make_web = gr.Checkbox(True, label="Also make a web-friendly MP4")
                        keep_audio = gr.Checkbox(True, label="Keep the original audio")

                with gr.Column(scale=1):
                    status = gr.Markdown("Ready.")
                    result_video = gr.Video(label="Result", interactive=False)
                    web_video = gr.Video(label="Web version", interactive=False)
                    downloads = gr.File(label="Download", file_count="multiple")

            video_in.change(_describe, inputs=video_in, outputs=details)

            def _handler(v, s, p, t, pr, mw, ka, progress=gr.Progress()):
                return _run(v, s, p, t, pr, mw, ka, progress=progress)

            go.click(
                _handler,
                inputs=[video_in, seconds, provider, token, prompt, make_web, keep_audio],
                outputs=[status, result_video, web_video, downloads],
            )
            stop.click(_cancel, outputs=status)

        gr.Markdown(
            "---\n"
            "**What to expect.** The free GPU allowance is about 5 minutes of GPU time "
            "per day with a free Hugging Face account (2 minutes without one). A "
            "10-second extension uses most of a day's allowance, and the cloud Space "
            "may queue behind other users. The continuation is generated at a lower "
            "resolution than most source video and scaled up, so it will look softer "
            "than the original. Your original file is never modified."
        )
    return app


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    app = build_app()
    # In a Codespace there is no local browser to open and the server must bind
    # to 0.0.0.0 so the forwarded port works; both are set via the environment.
    app.launch(
        server_name=os.environ.get("VIDEOEXTEND_HOST", "127.0.0.1"),
        server_port=int(os.environ.get("VIDEOEXTEND_PORT", "7860")),
        show_error=True,
        inbrowser=os.environ.get("VIDEOEXTEND_OPEN_BROWSER", "1") != "0",
        # Without this Gradio refuses to hand the finished files back to the
        # browser, and every output box shows an error instead of the video.
        allowed_paths=[str(OUTPUT_DIR)],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
