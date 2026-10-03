# Video

Animated videos written as code: HTML + CSS + GSAP compositions rendered to MP4 by
[HyperFrames](https://hyperframes.heygen.com).

Everything here is already installed and verified working in this container.

## Quick start

```bash
# 1. Scaffold a project (landscape | portrait | square | landscape-4k | portrait-4k)
./scripts/new-video.sh my-video landscape

# 2. Edit projects/my-video/index.html

# 3. Validate — lint + runtime + layout + motion + contrast
cd projects/my-video && npx hyperframes check

# 4. Render
npx hyperframes render        # -> renders/my-video_<timestamp>.mp4
```

Or just ask Claude Code: *"Using /hyperframes, make a 15-second intro about X."*
The skills in `.claude/skills/` route the request automatically.

## Layout

| Path | What it is |
| --- | --- |
| `.claude/skills/` | 21 HyperFrames skills (symlinks into `.agents/skills/`) |
| `.agents/skills/` | The skill sources, portable to other agent tools |
| `projects/` | One folder per video |
| `projects/mosaic-demo/` | Reference template — a working, validated composition |
| `vendor/gsap/` | GSAP 3.14.2, vendored (see *Why vendored* below) |
| `scripts/new-video.sh` | Scaffolds a project and vendors GSAP into it |
| `tools/video-extend/` | Separate tool: extend an existing MP4 with an AI continuation |

## Two separate things in this repo

**Making videos from code** (HyperFrames) — everything above.

**Extending an existing video** (`tools/video-extend/`) — takes an MP4 you
already have and adds an AI-generated continuation, so a 10s clip becomes ~20s
that plays as one shot. The AI runs on free cloud GPUs, not your machine.

Easiest way, with nothing installed locally: open this repo on GitHub → **Code**
→ **Codespaces** → **Create codespace**, wait for it to say `Ready.`, then run
`./extend-video.sh`. It is independent of HyperFrames; see
[its README](tools/video-extend/README.md) for the free-tier limits, which are
real and worth reading first.

## A composition in one minute

A composition is an HTML file. Timing lives in `data-*` attributes; animation lives in a
single **paused** GSAP timeline registered on `window.__timelines`:

```html
<div id="root" data-composition-id="main"
     data-width="1920" data-height="1080" data-duration="6">
  <section class="clip" data-start="0" data-duration="6">…</section>
</div>
<script>
  const tl = gsap.timeline({ paused: true });
  tl.from("#title", { y: 48, opacity: 0, duration: 0.6 }, 0.2);
  window.__timelines["main"] = tl;   // key MUST match data-composition-id
</script>
```

The renderer **seeks** that timeline frame by frame — it never plays it in real time.
That is why animation must be deterministic.

### Rules that bite first

- One paused timeline per composition, keyed to its `data-composition-id`.
- No `Date.now()`, no unseeded `Math.random()`, no network fetches at render time.
  (Seed a PRNG instead — `projects/mosaic-demo/compositions/mosaic.html` shows how.)
- Never set a CSS `transform` and then GSAP-tween the same property. Use `fromTo`.
- Never tween `visibility`/`autoAlpha` on a `.clip` — the framework owns clip visibility.
- Sub-compositions wrap their root in `<template>`, and their `<style>`/`<script>` must be
  **inside** that template.
- Run `npx hyperframes check` after every edit. A lint *error* silently disables the layout
  and contrast audits, so "0 issues" under a failing lint means nothing ran.

Full contract: `.claude/skills/hyperframes-core/`.

## Why GSAP is vendored

The scaffold's default composition loads GSAP from a CDN. Behind this container's HTTPS
proxy, headless Chrome rejects the proxy's TLS certificate (`ERR_CERT_AUTHORITY_INVALID`)
and the render fails with `gsap is not defined`.

Vendoring avoids that, and is better practice anyway: renders stay byte-reproducible and
work offline. Compositions load it with:

```html
<script src="assets/vendor/gsap.min.js"></script>
```

`scripts/new-video.sh` copies it into every new project. If you add a GSAP plugin, vendor it
the same way rather than reaching for the CDN.

## Container notes

- **Chrome is not in this repo.** HyperFrames downloads its own headless shell to
  `~/.cache/hyperframes/`, which is outside the repo and does **not** survive a new
  container. Re-run `npx hyperframes browser ensure` (~114 MB) after a fresh start.
- `npx hyperframes doctor` reports what is and isn't available.
- Optional extras are **not** installed: `whisper-cpp` (transcription), Kokoro (TTS),
  MusicGen (background music). Install them only if you need voiceover or captions.
- Rendering here uses a **software GPU** (no WebGL), so heavy 3D/shader work will be slow.
  The 6s demo renders in ~15s.
- Telemetry is **on by default** in HyperFrames. Turn it off with
  `npx hyperframes telemetry disable`.
- Cloud rendering (`hyperframes cloud`) needs a HeyGen account; local rendering does not.

## Useful commands

```bash
npx hyperframes check          # full validation gate
npx hyperframes preview --background   # Studio preview server
npx hyperframes snapshot --at 1,3,5    # PNG frames for a quick look
npx hyperframes timeline       # list tracks and clips
npx hyperframes docs <topic>   # offline docs
npx hyperframes render --help  # fps, codec, quality flags
```
