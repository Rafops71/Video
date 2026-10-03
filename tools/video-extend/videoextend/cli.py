"""Command-line interface.

    python -m videoextend info    my.mp4
    python -m videoextend check
    python -m videoextend extend  my.mp4 --seconds 10
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .errors import CancelledError, VideoExtendError
from .pipeline import Cancellation, extend_video
from .probe import probe
from .providers import DEFAULT_PROVIDER, available, get_provider


def _bar(message: str, fraction: float) -> None:
    width = 28
    filled = int(width * fraction)
    sys.stderr.write(f"\r  [{'█' * filled}{'░' * (width - filled)}] {message:<48}")
    sys.stderr.flush()
    if fraction >= 1.0:
        sys.stderr.write("\n")


def cmd_info(args) -> int:
    info = probe(args.video)
    print(f"File:        {Path(info.path).name}")
    print(f"Duration:    {info.duration:.2f} seconds")
    print(f"Resolution:  {info.width}x{info.height} ({info.orientation})")
    print(f"Frame rate:  {info.fps:.2f} fps")
    print(f"Video codec: {info.video_codec}")
    print(f"Audio:       {info.audio_codec if info.has_audio else 'none'}")
    return 0


def cmd_providers(args) -> int:
    for p in available():
        default = "  (default)" if p.key == DEFAULT_PROVIDER else ""
        print(f"{p.key}{default}")
        print(f"  {p.name} — {p.backend}")
        print(f"  Max {p.max_segment_seconds:.2f}s per call at {p.native_fps:.0f} fps")
        print(f"  Free: {'yes' if p.free else 'no'} · "
              f"Token required: {'yes' if p.requires_token else 'no'}")
        print(f"  {p.notes}\n")
    return 0


def cmd_check(args) -> int:
    """Verify the cloud provider is reachable without spending GPU quota."""
    provider = get_provider(args.provider, token=args.token)
    ok, message = provider.preflight()
    print(("OK  " if ok else "FAIL ") + message)
    return 0 if ok else 1


def cmd_extend(args) -> int:
    cancel = Cancellation()
    try:
        result = extend_video(
            args.video,
            extend_seconds=args.seconds,
            output_path=args.output,
            provider=args.provider,
            token=args.token,
            prompt=args.prompt,
            seed=args.seed,
            make_web_version=not args.no_web,
            keep_audio=not args.no_audio,
            progress_cb=None if args.quiet else _bar,
            cancellation=cancel,
        )
    except KeyboardInterrupt:
        cancel.cancel()
        print("\nCancelled.", file=sys.stderr)
        return 130
    except CancelledError:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except VideoExtendError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1

    print(f"\nOriginal:  {result.source.summary()}")
    print(f"Result:    {result.result.summary()}")
    print(f"Saved to:  {result.output_path}")
    if result.web_path:
        print(f"Web copy:  {result.web_path}")
    print(f"Generated: {result.segments_generated} continuation clip(s)")
    for w in result.warnings:
        print(f"\nNote: {w}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="videoextend",
        description="Extend an existing MP4 with an AI-generated continuation "
                    "(GPU work happens in the cloud, not on this machine).",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    p_info = sub.add_parser("info", help="Show a video's properties")
    p_info.add_argument("video")
    p_info.set_defaults(func=cmd_info)

    p_prov = sub.add_parser("providers", help="List cloud providers")
    p_prov.set_defaults(func=cmd_providers)

    p_check = sub.add_parser("check", help="Test the cloud connection (no GPU used)")
    p_check.add_argument("--provider", default=None)
    p_check.add_argument("--token", default=None, help="Hugging Face token (optional)")
    p_check.set_defaults(func=cmd_check)

    p_ext = sub.add_parser("extend", help="Extend a video")
    p_ext.add_argument("video")
    p_ext.add_argument("--seconds", type=float, default=10.0,
                       help="How much to add (default: 10)")
    p_ext.add_argument("--output", default=None)
    p_ext.add_argument("--provider", default=None)
    p_ext.add_argument("--token", default=None, help="Hugging Face token (optional)")
    p_ext.add_argument("--prompt", default=None)
    p_ext.add_argument("--seed", type=int, default=42)
    p_ext.add_argument("--no-web", action="store_true", help="Skip the web-friendly copy")
    p_ext.add_argument("--no-audio", action="store_true", help="Drop the original audio")
    p_ext.add_argument("--quiet", action="store_true")
    p_ext.set_defaults(func=cmd_extend)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except VideoExtendError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
