# Working in this repo

This repo makes animated videos from code: HTML/CSS/GSAP compositions rendered to MP4 by
HyperFrames. See `README.md` for the human-facing overview.

## Start at the skill router

For **any** request to make, edit, animate, or render a video, read `/hyperframes` first.
It routes to the owning workflow (`/motion-graphics`, `/general-video`,
`/product-launch-video`, `/faceless-explainer`, `/slideshow`, …) and loads the domain
skills. Do not author composition HTML from memory — read `/hyperframes-core` first.

## Project conventions

- New projects go in `projects/<name>/`, created with `./scripts/new-video.sh <name> [resolution]`.
  Don't call `hyperframes init` directly — the script also vendors GSAP, which renders need.
- **Load GSAP from `assets/vendor/gsap.min.js`, never a CDN.** Headless Chrome rejects this
  container's HTTPS-proxy certificate, so a CDN `<script>` fails at render time with
  `gsap is not defined`. Same rule for GSAP plugins and any other runtime library.
- `renders/` is gitignored. Output is reproducible, so don't commit MP4s.

## Before saying a composition is done

Run `npx hyperframes check` in the project directory and get a clean pass on all five
gates (lint, runtime, layout, motion, contrast). Two traps:

- A lint **error** switches off the layout and contrast audits. They then report
  `0 sample(s)` and `0/0 text checks`, which looks clean but means nothing ran. Clear lint
  errors before trusting those numbers.
- `check` passing is not the same as the video looking right. For anything non-trivial,
  render and inspect actual frames (`ffmpeg -ss <t> -i <mp4> -frames:v 1 out.png`) or use
  `npx hyperframes snapshot --at <times>`.

Render only when the work is ready — renders are cheap (~15s for 6s of 1080p) but not free.

## Determinism is a hard requirement

The renderer seeks the timeline frame by frame rather than playing it. Anything that varies
between seeks produces corrupt output:

- No `Date.now()`, no `performance.now()`, no unseeded `Math.random()`, no network at render time.
- Seed any randomness (see `projects/mosaic-demo/compositions/mosaic.html`).
- `repeat: -1` only under a finite root `data-duration`.

## Composition rules worth re-reading

- Exactly one `gsap.timeline({ paused: true })` per composition, registered at
  `window.__timelines["<id>"]` where `<id>` matches the root's `data-composition-id`.
  The master composition needs its own registered timeline even when all visible content
  lives in sub-compositions — child timelines auto-nest, but a missing master registration
  is a lint error.
- Sub-compositions wrap their root in `<template>`; their `<style>`/`<script>` go **inside**
  it (the assembler drops the file's own `<head>` for templated sub-comps).
- Never pair a CSS `transform` with a GSAP tween on the same property (`gsap_css_transform_conflict`).
  Set the initial state inside `fromTo`.
- Never tween `display`/`visibility`/`autoAlpha` on a `.clip`. Animate a child.
- Keep `id`s unique across the *assembled* page; prefix sub-comp ids with the composition id.
- A named `font-family` needs an in-file `@font-face` pointing at a shipped local file, or
  lint fires `font_family_without_font_face`. Generic stacks (`system-ui, sans-serif`) are fine.

## Environment

- Chrome lives in `~/.cache/hyperframes/`, outside the repo — it does not survive a new
  container. If rendering fails with a missing-Chrome error, run `npx hyperframes browser ensure`.
- Software GPU only (no WebGL). Prefer CSS/2D over heavy 3D or shader work.
- Not installed: whisper-cpp, Kokoro TTS, MusicGen. Anything needing transcription,
  voiceover, or generated music requires installing those first — say so rather than
  silently routing around it.
