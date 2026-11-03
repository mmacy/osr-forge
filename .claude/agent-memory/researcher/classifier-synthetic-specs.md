---
name: classifier-synthetic-specs
description: Classifier spec settings that make the synthetic TSR blue and black and white maps read correctly (faint grid lines, hatched rock)
metadata:
  type: project
---

The synthetic crypt (TSR blue, JPEG) prints its grid lines at red-channel level 199 to 215 on paper at 235 to 250, so the default `line_ink_max` of 175 found 6 of 20 horizontal lines and the fit got the wrong pitch; `line_ink_max` 222 finds them all (rms 0.027 cells). The synthetic manor draws rock as diagonal hatching, which the classifier read as floor (136/396 cells right) until `levels.hatched: true` turned long thin diagonal runs and the paper between them into fill (395/396). Both settings are in `tools/answer_keys/synthetic-maps.json`.

**Why:** the pipeline check on synthetic maps is only meaningful when the classifier reading is sound; these two settings were the whole difference.

**How to apply:** when a new synthetic or real map reads badly, look at the pixel levels of its grid lines and at whether rock is hatched before touching the classifier's rules.
