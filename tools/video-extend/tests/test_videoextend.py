"""Tests for the video-extension feature.

Run with:  python3 -m unittest discover -s tests -v

These cover everything that does not need the cloud GPU. The live cloud call is
covered separately by `tests/test_cloud_live.py`, which is opt-in because it
spends real daily quota.
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from videoextend import media
from videoextend.errors import (
    CancelledError, InvalidVideoError, ProviderError, QuotaExceededError,
    VideoExtendError,
)
from videoextend.pipeline import Cancellation, extend_video
from videoextend.probe import probe
from videoextend.providers import available, get_provider
from videoextend.providers.base import VideoProvider

from .fakes import FakeProvider, make_test_video


def sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class TempCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)


# --- 4. Video metadata detection -------------------------------------------

class TestProbe(TempCase):
    def test_reads_metadata(self):
        src = make_test_video(self.tmp / "a.mp4", seconds=4, width=640,
                              height=360, fps=24)
        info = probe(src)
        self.assertAlmostEqual(info.duration, 4.0, delta=0.3)
        self.assertEqual((info.width, info.height), (640, 360))
        self.assertAlmostEqual(info.fps, 24.0, delta=0.1)
        self.assertEqual(info.orientation, "landscape")
        self.assertFalse(info.has_audio)

    def test_detects_audio(self):
        src = make_test_video(self.tmp / "b.mp4", seconds=2, audio=True)
        self.assertTrue(probe(src).has_audio)

    def test_orientation(self):
        portrait = make_test_video(self.tmp / "p.mp4", seconds=1, width=360, height=640)
        square = make_test_video(self.tmp / "s.mp4", seconds=1, width=320, height=320)
        self.assertEqual(probe(portrait).orientation, "portrait")
        self.assertEqual(probe(square).orientation, "square")


# --- 10. Invalid video files ------------------------------------------------

class TestInvalidInput(TempCase):
    def test_missing_file(self):
        with self.assertRaises(InvalidVideoError):
            probe(self.tmp / "nope.mp4")

    def test_empty_file(self):
        p = self.tmp / "empty.mp4"
        p.write_bytes(b"")
        with self.assertRaises(InvalidVideoError):
            probe(p)

    def test_not_a_video(self):
        p = self.tmp / "notes.txt"
        p.write_text("I am text, not video.")
        with self.assertRaises(InvalidVideoError):
            probe(p)

    def test_truncated_video(self):
        src = make_test_video(self.tmp / "ok.mp4", seconds=2)
        bad = self.tmp / "bad.mp4"
        bad.write_bytes(src.read_bytes()[:400])
        with self.assertRaises(InvalidVideoError):
            probe(bad)

    def test_directory_rejected(self):
        with self.assertRaises(InvalidVideoError):
            probe(self.tmp)


# --- segment planning -------------------------------------------------------

class TestPlanning(unittest.TestCase):
    def test_splits_into_capped_segments(self):
        p = FakeProvider(max_segment_seconds=3.375)
        plan = p.plan_segments(10.0)
        self.assertGreaterEqual(len(plan), 3)
        self.assertTrue(all(s <= 3.375 + 1e-6 for s in plan))
        self.assertAlmostEqual(sum(plan), 10.0, places=2)

    def test_no_tiny_sliver(self):
        p = FakeProvider(max_segment_seconds=5.0)
        for total in (10.2, 10.1, 7.3, 15.4):
            with self.subTest(total=total):
                plan = p.plan_segments(total)
                self.assertAlmostEqual(sum(plan), total, places=2)
                self.assertTrue(all(s >= 0.5 for s in plan), plan)

    def test_shorter_than_cap(self):
        self.assertEqual(len(FakeProvider(max_segment_seconds=5.0).plan_segments(2.0)), 1)

    def test_resolution_fits_budget(self):
        p = FakeProvider()
        w, h = p.fit_resolution(1920, 1080)
        self.assertLessEqual(w * h, 480 * 832 * 1.15)
        self.assertEqual(w % 32, 0)
        self.assertEqual(h % 32, 0)
        # Aspect ratio preserved within rounding.
        self.assertAlmostEqual(w / h, 1920 / 1080, delta=0.08)

    def test_small_resolution_untouched_but_snapped(self):
        w, h = FakeProvider().fit_resolution(320, 320)
        self.assertEqual((w % 32, h % 32), (0, 0))


# --- 5. Extension workflow ---------------------------------------------------

class TestPipeline(TempCase):
    def test_extends_and_joins(self):
        src = make_test_video(self.tmp / "src.mp4", seconds=4, width=640,
                              height=360, fps=24)
        provider = FakeProvider(max_segment_seconds=3.375)
        out = self.tmp / "out.mp4"

        result = extend_video(src, extend_seconds=6.0, output_path=out,
                              provider=provider, make_web_version=False)

        self.assertEqual(len(provider.calls), 2)          # 6s / 3.375 -> 2 clips
        self.assertAlmostEqual(result.result.duration, 10.0, delta=1.0)
        # 12. Playable, and 13. correct duration.
        info = media.verify(out)
        self.assertGreater(info.duration, result.source.duration)

    def test_output_matches_source_format(self):
        src = make_test_video(self.tmp / "src.mp4", seconds=3, width=854,
                              height=480, fps=30)
        out = self.tmp / "out.mp4"
        extend_video(src, extend_seconds=3.0, output_path=out,
                     provider=FakeProvider(), make_web_version=False)
        info = probe(out)
        self.assertEqual((info.width, info.height), (854, 480))
        self.assertAlmostEqual(info.fps, 30.0, delta=0.5)

    def test_chains_from_previous_frame(self):
        """Each clip after the first must be seeded by a *new* anchor image."""
        src = make_test_video(self.tmp / "src.mp4", seconds=2)
        provider = FakeProvider(max_segment_seconds=2.0)
        extend_video(src, extend_seconds=6.0, output_path=self.tmp / "o.mp4",
                     provider=provider, make_web_version=False)
        anchors = [c.image_path for c in provider.calls]
        self.assertEqual(len(anchors), 3)
        self.assertEqual(len(set(anchors)), 3, "each segment needs its own anchor frame")

    # --- 11. Original stays untouched ---------------------------------------
    def test_original_never_modified(self):
        src = make_test_video(self.tmp / "src.mp4", seconds=3)
        before = sha256(src)
        extend_video(src, extend_seconds=3.0, output_path=self.tmp / "out.mp4",
                     provider=FakeProvider(), make_web_version=False)
        self.assertEqual(sha256(src), before)

    def test_refuses_to_overwrite_original(self):
        src = make_test_video(self.tmp / "src.mp4", seconds=2)
        with self.assertRaises(Exception):
            extend_video(src, extend_seconds=2.0, output_path=src,
                         provider=FakeProvider(), make_web_version=False)
        self.assertTrue(src.exists())

    def test_default_output_is_beside_source(self):
        src = make_test_video(self.tmp / "clip.mp4", seconds=2)
        result = extend_video(src, extend_seconds=2.0, provider=FakeProvider(),
                              make_web_version=False)
        self.assertEqual(Path(result.output_path).name, "clip_extended.mp4")
        self.assertTrue(Path(result.output_path).exists())

    def test_web_version_created(self):
        src = make_test_video(self.tmp / "src.mp4", seconds=2)
        result = extend_video(src, extend_seconds=2.0, output_path=self.tmp / "o.mp4",
                              provider=FakeProvider(), make_web_version=True)
        self.assertIsNotNone(result.web_path)
        self.assertTrue(Path(result.web_path).exists())
        probe(result.web_path)  # Must be playable.

    def test_audio_preserved_when_present(self):
        src = make_test_video(self.tmp / "src.mp4", seconds=3, audio=True)
        out = self.tmp / "out.mp4"
        extend_video(src, extend_seconds=3.0, output_path=out,
                     provider=FakeProvider(), make_web_version=False)
        self.assertTrue(probe(out).has_audio)

    def test_silent_source_stays_silent(self):
        src = make_test_video(self.tmp / "src.mp4", seconds=2, audio=False)
        out = self.tmp / "out.mp4"
        extend_video(src, extend_seconds=2.0, output_path=out,
                     provider=FakeProvider(), make_web_version=False)
        self.assertFalse(probe(out).has_audio)

    def test_progress_reported(self):
        src = make_test_video(self.tmp / "src.mp4", seconds=2)
        seen = []
        extend_video(src, extend_seconds=2.0, output_path=self.tmp / "o.mp4",
                     provider=FakeProvider(), make_web_version=False,
                     progress_cb=lambda m, f: seen.append((m, f)))
        self.assertTrue(seen)
        self.assertAlmostEqual(seen[-1][1], 1.0)
        self.assertTrue(all(0.0 <= f <= 1.0 for _, f in seen))


# --- 8. Failure handling -----------------------------------------------------

class TestFailures(TempCase):
    def test_first_call_failure_propagates(self):
        src = make_test_video(self.tmp / "src.mp4", seconds=2)
        with self.assertRaises(ProviderError):
            extend_video(src, extend_seconds=6.0, output_path=self.tmp / "o.mp4",
                         provider=FakeProvider(fail_after=0), make_web_version=False)

    def test_partial_failure_keeps_what_worked(self):
        """Quota running out mid-job should still deliver a usable video."""
        src = make_test_video(self.tmp / "src.mp4", seconds=3)
        out = self.tmp / "o.mp4"
        result = extend_video(
            src, extend_seconds=9.0, output_path=out,
            provider=FakeProvider(max_segment_seconds=3.0, fail_after=2,
                                  quota_error=True),
            make_web_version=False,
        )
        self.assertEqual(result.segments_generated, 2)
        self.assertTrue(any("quota" in w.lower() or "shorter" in w.lower()
                            for w in result.warnings))
        self.assertTrue(out.exists())

    def test_quota_error_is_specific(self):
        self.assertTrue(issubclass(QuotaExceededError, ProviderError))

    def test_invalid_source_rejected_before_cloud_call(self):
        bad = self.tmp / "bad.mp4"
        bad.write_text("nope")
        provider = FakeProvider()
        with self.assertRaises(InvalidVideoError):
            extend_video(bad, extend_seconds=5.0, provider=provider)
        self.assertEqual(provider.calls, [], "must not call the cloud for a bad file")

    def test_zero_seconds_rejected(self):
        src = make_test_video(self.tmp / "src.mp4", seconds=2)
        with self.assertRaises(Exception):
            extend_video(src, extend_seconds=0, provider=FakeProvider())


# --- 9. Cancellation ---------------------------------------------------------

class TestCancellation(TempCase):
    def test_cancel_before_start(self):
        src = make_test_video(self.tmp / "src.mp4", seconds=2)
        cancel = Cancellation()
        cancel.cancel()
        provider = FakeProvider()
        with self.assertRaises(CancelledError):
            extend_video(src, extend_seconds=6.0, output_path=self.tmp / "o.mp4",
                         provider=provider, cancellation=cancel)
        self.assertEqual(provider.calls, [])

    def test_cancel_between_segments(self):
        src = make_test_video(self.tmp / "src.mp4", seconds=2)
        cancel = Cancellation()

        class CancellingProvider(FakeProvider):
            def generate(self, request, out_path):
                out = super().generate(request, out_path)
                cancel.cancel()      # Stop after the first clip.
                return out

        provider = CancellingProvider(max_segment_seconds=2.0)
        with self.assertRaises(CancelledError):
            extend_video(src, extend_seconds=6.0, output_path=self.tmp / "o.mp4",
                         provider=provider, cancellation=cancel)
        self.assertEqual(len(provider.calls), 1)


# --- media helpers -----------------------------------------------------------

class TestMedia(TempCase):
    def test_last_frame_extracted(self):
        src = make_test_video(self.tmp / "src.mp4", seconds=3)
        png = media.last_frame(src, self.tmp / "last.png")
        self.assertTrue(png.exists())
        self.assertGreater(png.stat().st_size, 1000)

    def test_normalize_forces_size_and_fps(self):
        src = make_test_video(self.tmp / "src.mp4", seconds=2, width=320,
                              height=240, fps=15)
        out = media.normalize(src, self.tmp / "n.mp4", 640, 360, 30.0)
        info = probe(out)
        self.assertEqual((info.width, info.height), (640, 360))
        self.assertAlmostEqual(info.fps, 30.0, delta=0.5)

    def test_concat_adds_durations(self):
        a = make_test_video(self.tmp / "a.mp4", seconds=2, fps=24)
        b = make_test_video(self.tmp / "b.mp4", seconds=2, fps=24)
        na = media.normalize(a, self.tmp / "na.mp4", 320, 240, 24)
        nb = media.normalize(b, self.tmp / "nb.mp4", 320, 240, 24)
        out = media.concat([na, nb], self.tmp / "c.mp4")
        self.assertAlmostEqual(probe(out).duration, 4.0, delta=0.6)

    def test_verify_rejects_wrong_duration(self):
        src = make_test_video(self.tmp / "src.mp4", seconds=2)
        with self.assertRaises(InvalidVideoError):
            media.verify(src, expect_seconds=30.0, tolerance=1.0)

    def test_even_dimensions(self):
        self.assertEqual(media.even(1081), 1080)
        self.assertEqual(media.even(1080), 1080)


# --- 6. Provider registry / abstraction --------------------------------------

class TestProviders(unittest.TestCase):
    def test_registry_lists_providers(self):
        infos = available()
        self.assertGreaterEqual(len(infos), 2)
        for i in infos:
            self.assertTrue(i.free, f"{i.key} must be free")
            self.assertGreater(i.max_segment_seconds, 0)

    def test_get_provider_returns_instance(self):
        self.assertIsInstance(get_provider(), VideoProvider)

    def test_unknown_provider_rejected(self):
        with self.assertRaises(KeyError):
            get_provider("does-not-exist")

    def test_verified_limits_match_space_source(self):
        """Guards against silently drifting from the values read off the Spaces."""
        by_key = {i.key: i for i in available()}
        i2v = by_key["wan-i2v-fast"]
        self.assertAlmostEqual(i2v.max_segment_seconds, 81 / 24, places=3)
        self.assertEqual(i2v.native_fps, 24.0)
        vace = by_key["wan-vace-fast"]
        self.assertAlmostEqual(vace.max_segment_seconds, 81 / 16, places=3)
        self.assertEqual(vace.native_fps, 16.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)


# --- error classification (uses the real message the Space returned) ---------

class TestErrorClassification(unittest.TestCase):
    """The live Space returned these exact strings during development."""

    def test_zerogpu_quota_message_detected(self):
        from videoextend.providers.hf_space import _classify

        class AppError(Exception):
            def __init__(self, message, title):
                super().__init__(message)
                self.message, self.title = message, title

        exc = AppError(
            "You have exceeded your ZeroGPU runs limit. Authenticate with a "
            "Hugging Face token for more quota - "
            "https://huggingface.co/settings/tokens",
            "ZeroGPU quota exceeded",
        )
        self.assertIsInstance(_classify(exc), QuotaExceededError)

    def test_quota_detected_from_title_only(self):
        """str(exc) is sometimes just 'RuntimeError'; the title still tells us."""
        from videoextend.providers.hf_space import _classify

        class AppError(Exception):
            def __init__(self):
                super().__init__("RuntimeError")
                self.message = "RuntimeError"
                self.title = "ZeroGPU quota exceeded"

        self.assertIsInstance(_classify(AppError()), QuotaExceededError)

    def test_generic_failure_is_not_quota(self):
        from videoextend.providers.hf_space import _classify
        result = _classify(RuntimeError("connection reset by peer"))
        self.assertIsInstance(result, ProviderError)
        self.assertNotIsInstance(result, QuotaExceededError)


# --- output naming (a real failure: Grok filenames are base64 blobs) ---------

class TestOutputNaming(unittest.TestCase):
    def test_safe_stem_shortens_and_sanitises(self):
        from videoextend.ui import _safe_stem
        long_name = "a" * 300 + ".mp4"
        self.assertLessEqual(len(_safe_stem(long_name)), 40)
        self.assertEqual(_safe_stem("my video (final)!.mp4"), "my_video_final")
        # A name with nothing usable in it still has to yield something.
        self.assertEqual(_safe_stem("!!!.mp4"), "video")
        self.assertTrue(_safe_stem(".mp4"))

    def test_safe_stem_keeps_readable_names(self):
        from videoextend.ui import _safe_stem
        self.assertEqual(_safe_stem("/tmp/x/Jericho.mp4"), "Jericho")

    def test_long_upload_name_still_produces_usable_output(self):
        """A 200+ char upload name must not produce an unopenable output path."""
        from videoextend.ui import _safe_stem
        stem = _safe_stem("b" * 250 + ".mp4")
        name = f"{stem}_120000_extended_web.mp4"
        self.assertLess(len(name.encode()), 255)


# --- guided route (generation done by hand in the browser) -------------------

class TestGuidedRoute(TempCase):
    def test_starting_frame_is_full_resolution(self):
        from videoextend.pipeline import starting_frame
        src = make_test_video(self.tmp / "s.mp4", seconds=4, width=1376, height=928)
        png = starting_frame(src, self.tmp / "f.png")
        self.assertTrue(png.exists())
        self.assertGreater(png.stat().st_size, 1000)

    def test_assembles_user_supplied_clips(self):
        from videoextend.pipeline import assemble_extension
        src = make_test_video(self.tmp / "s.mp4", seconds=6, width=1376,
                              height=928, fps=24, audio=True)
        c1 = make_test_video(self.tmp / "c1.mp4", seconds=3, width=832, height=480)
        c2 = make_test_video(self.tmp / "c2.mp4", seconds=3, width=832, height=480)

        out = self.tmp / "out.mp4"
        result = assemble_extension(src, [c1, c2], output_path=out,
                                    make_web_version=False)
        self.assertEqual(result.segments_generated, 2)
        info = probe(out)
        # Source resolution and frame rate are preserved, clips are scaled up.
        self.assertEqual((info.width, info.height), (1376, 928))
        self.assertAlmostEqual(info.duration, 12.0, delta=1.0)
        self.assertTrue(info.has_audio)

    def test_assemble_rejects_empty_clip_list(self):
        from videoextend.pipeline import assemble_extension
        src = make_test_video(self.tmp / "s.mp4", seconds=2)
        with self.assertRaises(VideoExtendError):
            assemble_extension(src, [], output_path=self.tmp / "o.mp4")

    def test_assemble_rejects_bad_clip(self):
        from videoextend.pipeline import assemble_extension
        src = make_test_video(self.tmp / "s.mp4", seconds=2)
        bad = self.tmp / "bad.mp4"
        bad.write_text("not a video")
        with self.assertRaises(InvalidVideoError):
            assemble_extension(src, [bad], output_path=self.tmp / "o.mp4")

    def test_assemble_leaves_original_untouched(self):
        from videoextend.pipeline import assemble_extension
        src = make_test_video(self.tmp / "s.mp4", seconds=3)
        clip = make_test_video(self.tmp / "c.mp4", seconds=2)
        before = sha256(src)
        assemble_extension(src, [clip], output_path=self.tmp / "o.mp4",
                           make_web_version=False)
        self.assertEqual(sha256(src), before)


class TestStartingFrameSizing(TempCase):
    """Full-size stills make the free GPU Spaces fail with a RuntimeError."""

    def _dims(self, png):
        import subprocess
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
             "-of", "csv=p=0", str(png)], capture_output=True, text=True).stdout
        return tuple(int(x) for x in out.strip().split(","))

    def test_large_frame_is_shrunk_to_model_budget(self):
        from videoextend.pipeline import starting_frame
        src = make_test_video(self.tmp / "big.mp4", seconds=3, width=1376, height=928)
        w, h = self._dims(starting_frame(src, self.tmp / "f.png"))
        self.assertLessEqual(w * h, 480 * 832)
        self.assertEqual((w % 32, h % 32), (0, 0))
        self.assertAlmostEqual(w / h, 1376 / 928, delta=0.05)

    def test_small_frame_left_alone(self):
        from videoextend.pipeline import starting_frame
        src = make_test_video(self.tmp / "small.mp4", seconds=3, width=640, height=360)
        self.assertEqual(self._dims(starting_frame(src, self.tmp / "f.png")), (640, 360))

    def test_full_resolution_still_available(self):
        from videoextend.pipeline import starting_frame
        src = make_test_video(self.tmp / "big.mp4", seconds=3, width=1376, height=928)
        w, h = self._dims(starting_frame(src, self.tmp / "f.png", max_pixels=None))
        self.assertEqual((w, h), (1376, 928))
