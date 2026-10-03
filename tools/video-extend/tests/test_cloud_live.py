"""Live cloud tests. Opt-in, because they spend real daily GPU quota.

    # Connection only — no GPU time used:
    VIDEOEXTEND_LIVE=1 python3 -m unittest tests.test_cloud_live -v

    # Full generation — uses roughly 60-90s of your daily GPU allowance:
    VIDEOEXTEND_LIVE=1 VIDEOEXTEND_LIVE_GPU=1 python3 -m unittest tests.test_cloud_live -v

Set HF_TOKEN to use your free account's larger quota.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from videoextend.probe import probe
from videoextend.providers import get_provider
from videoextend.providers.base import GenerationRequest

from .fakes import make_test_video

LIVE = os.environ.get("VIDEOEXTEND_LIVE") == "1"
LIVE_GPU = os.environ.get("VIDEOEXTEND_LIVE_GPU") == "1"


@unittest.skipUnless(LIVE, "set VIDEOEXTEND_LIVE=1 to run live cloud tests")
class TestCloudConnection(unittest.TestCase):
    """Reachability only. Does not queue a GPU job, so costs no quota."""

    def test_default_provider_reachable(self):
        ok, message = get_provider().preflight()
        self.assertTrue(ok, message)

    def test_all_providers_reachable(self):
        for key in ("wan-i2v-fast", "wan-vace-fast"):
            with self.subTest(provider=key):
                ok, message = get_provider(key).preflight()
                self.assertTrue(ok, message)


@unittest.skipUnless(LIVE and LIVE_GPU,
                     "set VIDEOEXTEND_LIVE_GPU=1 to spend real GPU quota")
class TestCloudGeneration(unittest.TestCase):
    """Generates one short clip on the cloud GPU."""

    def test_generates_a_real_clip(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            src = make_test_video(tmp / "src.mp4", seconds=2, width=640, height=360)

            from videoextend import media
            anchor = media.last_frame(src, tmp / "anchor.png")

            provider = get_provider(token=os.environ.get("HF_TOKEN"))
            out = provider.generate(
                GenerationRequest(
                    image_path=str(anchor),
                    prompt="continue this shot, same scene, smooth motion",
                    seconds=2.0, width=640, height=360, seed=42,
                ),
                tmp / "generated.mp4",
            )

            info = probe(out)
            self.assertGreater(info.duration, 0.5)
            self.assertGreater(info.width, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
