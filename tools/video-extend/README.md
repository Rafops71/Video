# Extend Video

Take an existing MP4 and add an AI-generated continuation so it plays as one
longer, continuous clip. Built for website hero videos, where continuity matters
more than new content.

**The AI runs on free cloud GPUs. Your computer only does the joining.**

This is a separate tool. It does not touch the HyperFrames projects in this repo.

---

## How to use it

```bash
cd tools/video-extend
./run.sh
```

First run sets itself up (about a minute), then opens a page in your browser:

1. **Choose Video** — pick your MP4
2. It shows the duration, size and frame rate
3. Set **Extension** (default 10 seconds)
4. Press **EXTEND VIDEO**
5. Watch the progress, then **play** and **download** the result

Your original file is never modified. The result is saved next to it as
`yourvideo_extended.mp4`, plus `yourvideo_extended_web.mp4` for websites.

### You will want a free Hugging Face account

Not strictly required, but strongly recommended:

| | Free GPU time per day |
|---|---|
| Not signed in | 2 minutes |
| Free account | **5 minutes** |

A 10-second extension needs roughly 3–4 minutes of GPU time, so **without an
account it will not finish**. Signing up is free and takes no card:

1. Make an account at [huggingface.co/join](https://huggingface.co/join)
2. Create a read token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)
3. Paste it into **Advanced → Hugging Face token** in the app

The token is only held in memory for the session. It is never written to this
repository.

---

## Honest limits

Read this before expecting too much.

- **About one 10-second extension per day** on the free tier. The quota resets
  24 hours after your first use that day.
- **The continuation is generated at up to 832x480** and scaled up to your
  source resolution. It will look softer than your original. This is the
  model's limit, not a setting.
- **No sound is generated.** If your source has audio, it is kept and faded out
  over the last second before the join, so the transition is not an abrupt cut.
- **It is a shared public service.** The Space can be queued, asleep, or
  restarting. Trying again later usually works.
- **Continuity is good, not perfect.** The continuation starts from the exact
  last frame of your video, so the seam is smooth, but the AI invents what
  happens next. Chained segments drift further the longer you extend.
- Longer extensions mean more chained segments, more quota, and more drift.
  10 seconds is the sensible maximum on the free tier.

---

## How it works

```
Your video
   ↓ read duration / size / frame rate (ffprobe)
   ↓ take the LAST FRAME
Cloud video provider  ──→  Hugging Face Space on ZeroGPU (free shared GPU)
   ↓ generate a short clip starting FROM that frame
   ↓ take the last frame of that clip, generate the next one... (chained)
   ↓ scale every clip to your original size and frame rate
   ↓ join, carry audio, verify
Result + web-friendly copy
```

Using the last frame as the starting image is what makes it a continuation
rather than an unrelated clip: the model is doing image-to-video, so frame 0 of
the generated clip *is* the last frame of your video.

### Why it is chained

The free models cap out at 81 frames per call. That is 3.37 seconds at 24fps.
A 10-second extension is therefore 3 calls, each seeded by the previous clip's
final frame.

---

## Verified facts

Checked against the live services on 2026-10-03. Numbers read from official
docs and from the Spaces' own source, not assumed.

| Fact | Value | Source |
|---|---|---|
| ZeroGPU quota, not signed in | 2 min/day | [HF docs](https://huggingface.co/docs/hub/main/spaces-zerogpu) |
| ZeroGPU quota, free account | 5 min/day | same |
| ZeroGPU quota, PRO | 40 min/day (paid) | same |
| `multimodalart/wan2-1-fast` max frames | 81 @ 24fps = **3.375s** | Space `app.py` |
| `linoyts/wan2-1-VACE-fast` max frames | 81 @ 16fps = **5.06s** | Space `app.py` |
| Max generation area | 480 x 832 px | Space `app.py` |
| GPU cost per call | 60–90 s | Space `get_duration()` |

**UNVERIFIED:** whether these public Spaces stay available long-term. They are
run by community members and can be paused or changed at any time. The provider
abstraction exists so another backend can be swapped in without rewriting the
app.

### Why not VACE as the default

VACE was investigated as requested. It generates longer clips per call (5.06s,
so fewer calls and less quota) but its `Reference` mode treats the supplied
still as a *style and subject reference*, not as frame 0 — so the seam is
looser. Since continuity is the whole point here, the image-to-video Space is
the default. VACE is available as `--provider wan-vace-fast`.

---

## Command line

```bash
python3 -m videoextend info my.mp4          # show duration/size/fps
python3 -m videoextend providers            # list cloud backends
python3 -m videoextend check                # test the connection (no GPU used)
python3 -m videoextend extend my.mp4 --seconds 10
```

Useful flags: `--output`, `--provider`, `--token`, `--prompt`, `--seed`,
`--no-web`, `--no-audio`.

---

## Adding another cloud provider

Subclass `VideoProvider`, implement `generate()`, add one line to
`providers/__init__.py`. Nothing else changes.

```python
class MyProvider(VideoProvider):
    @property
    def info(self) -> ProviderInfo: ...
    def generate(self, request: GenerationRequest, out_path) -> Path: ...
```

---

## Tests

```bash
cd tools/video-extend
python3 -m unittest discover -s tests -t . -v      # 45 tests, no cloud needed

VIDEOEXTEND_LIVE=1 python3 -m unittest tests.test_cloud_live    # connection only
VIDEOEXTEND_LIVE=1 VIDEOEXTEND_LIVE_GPU=1 \
  python3 -m unittest tests.test_cloud_live                     # spends real quota
```

### What was tested, and what was not

**Tested and passing:** metadata detection, invalid/corrupt/empty/non-video
files, segment planning, resolution fitting, chaining (each clip seeded by a new
anchor frame), joining, resolution and frame-rate preservation, audio carry-over,
web export (confirmed faststart), output verification, failure handling, partial
failure, cancellation, the original file staying byte-identical, and live
connection to both Spaces.

**NOT tested:** a complete real AI generation. The anonymous ZeroGPU quota for
this development machine's shared IP was already exhausted, so the live GPU call
returned *"You have exceeded your ZeroGPU runs limit."* The error path was
verified against that real response; the success path was verified with an
offline stand-in provider that exercises the identical pipeline. **The first
genuine end-to-end generation will happen on your machine with your token.**
