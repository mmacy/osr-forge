---
name: structured-answer-checks
description: Check a model's structured answer for faults of substance and normalize faults of form, or the retries burn money on spelling
metadata:
  type: feedback
---

When a checker rejects a Claude answer, make sure the fault is one a retry can fix (a row of the wrong length, a letter outside the alphabet, a truncated answer), not a spelling of the same fact (`4,69 north` for `4,69:north`, a cave mouth listed with its edge's cell). Normalize the forms first, then check.

**Why:** on Chaotic Caves page 38 the window reads were asked up to five times each, mostly because the cave-mouth edge came back as `x,y north`, `x,y:n`, or `x,y,e`; rewriting those forms parsed 9 stored windows and 5 label windows at no cost, where the retries had cost about $1.50 and an hour.

**How to apply:** when a retry log shows the same window failing twice on the same field, read the raw answers before sending another request; if the content is right in a different form, widen the parser.
