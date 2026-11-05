---
name: register-before-keying
description: Run register_image on a map before keying it; a spec rect wider than the framed drawing shifts every key cell and the scorer's alignment
metadata:
  type: feedback
---

Before building an answer key, compare the map spec's working grid with `register_image`'s rectangle for the image, and record any difference as the spec's `frame`.

**Why:** on 2026-11-04 the Chaotic Caves manor keys turned out to start five and three rows above their registered grids, because the spec's `rect` covered the title and legend. The stores, samples, and every call were on the working grid, so the fix needed a translation layer (`frame.py`) instead of a one-line spec change. The scorer only aligns within three cells.

**How to apply:** at the start of a key, run `register_image` on the image and set `rect` or `frame` to match. A registration rectangle that contradicts `RegionFit`'s own contract (dropping a row the image edge cuts, as on page 38, or covering part of a grid, as on a Palace level) is a registration defect to report, not a frame. See [[measure-joins-before-handover]].
