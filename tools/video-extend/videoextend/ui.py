"""Simple local web interface.

Deliberately minimal: choose a video, choose how many seconds, press one button.
Technical controls (provider, seed, prompt) are tucked behind "Advanced" so the
normal path stays three clicks.
"""

from __future__ import annotations

import os
import threading
import traceback
from pathlib import Path

from .errors import CancelledError, VideoExtendError
from .pipeline import Cancellation, extend_video
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
            # Output beside the source, never over it.
            output_path=None,
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
    app = build_app()
    app.launch(server_name=os.environ.get("VIDEOEXTEND_HOST", "127.0.0.1"),
               server_port=int(os.environ.get("VIDEOEXTEND_PORT", "7860")),
               show_error=True, inbrowser=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
