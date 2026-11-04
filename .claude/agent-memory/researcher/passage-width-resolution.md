---
name: passage-width-resolution
description: Measure drawn passage widths at 24 samples per cell with Euclidean clearance; 12 samples with square erosion read about 0.1 cell low
metadata:
  type: feedback
---

Quote passage widths from `tools/answer_keys/passages.py` at `res=24`, which uses Euclidean clearance on the passable mask.

**Why:** at 12 samples per cell with square erosion, the Cave of Horrors' passages measured 0.39 to 0.42 of a cell; at 24 with Euclidean clearance they are 0.49 to 0.54, which moved them from "under half" to "about half" and into a different case (14, not 13). Diagonal passages were also understated by up to a factor of 1.4 under square erosion.

**How to apply:** use 24 for any figure that goes into a case or a report; 12 is fine for a first scan. Lines drawn over rock (streams, contours) read as passages 0.15 to 0.21 wide on page 38, so a threshold under a quarter cell joins dungeons wrongly.
