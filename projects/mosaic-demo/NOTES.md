# mosaic-demo — reference template

Not a deliverable. This exists to prove the render pipeline works end to end in this
container, and to serve as a copyable example of the patterns the framework expects.

Verified: `npx hyperframes check` passes all five gates; `npx hyperframes render` produces
1920x1080 h264, 30fps, 180 frames, ~15s render time.

## What it demonstrates

- **Master + sub-composition structure.** `index.html` mounts `compositions/mosaic.html`
  and `compositions/title.html` via `data-composition-src`. This is the structure the
  linter wants — a master with nested markup raises `nested_structure_needs_subcomposition`.
- **A master timeline that still does work.** Child timelines auto-nest, but the master
  must register its own or lint fails with `missing_timeline_registry`. Here it owns the
  vignette.
- **Deterministic randomness.** `mosaic.html` seeds an LCG rather than calling
  `Math.random()`, so tile tints are identical on every frame and every re-render.
- **Vendored GSAP.** `assets/vendor/gsap.min.js`, not a CDN — see the repo README.
- **Grid stagger.** `stagger: { from: "center", grid: [ROWS, COLS] }` for the bloom, then
  `from: "edges"` for the recede.

## Timing

| Time | What happens |
| --- | --- |
| 0.0-0.5s | 144 tiles bloom in from centre |
| 1.5-2.1s | Tiles recede to 12% opacity from the edges in |
| 1.6-2.8s | Vignette fades up (master timeline) |
| 2.1-3.0s | Headline, rule, subhead resolve |
| 4.0-5.6s | Slow settle so the last second isn't a frozen frame |

Render it yourself:

```bash
cd projects/mosaic-demo
npx hyperframes check
npx hyperframes render
```
