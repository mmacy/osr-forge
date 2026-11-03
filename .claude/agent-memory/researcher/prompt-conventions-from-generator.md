---
name: prompt-conventions-from-generator
description: Before writing a reading prompt's rules, check what the synthetic generator writes for every edge and cell class, because the conventions leave some unstated
metadata:
  type: feedback
---

Before a reading or settling prompt states a rule the conventions don't spell out (the letter between floor and water, between floor and the drawing's frame, around a dashed circle), read what the synthetic generator writes for that case in `tests/assets/synthetic-maps/*/geometry.json` and follow it; log a case when the generator and the conventions disagree.

**Why:** on 2026-11-02 the first prompts said an edge between floor and water is a wall; the generator writes `o` (the water cell is impassable on its own, and a crossing needs passable banks), so every bank of the manor's moat had to be asked again in an extra round. The frame edge and the dashed circle were the same kind of miss on the synthetic maps.

**How to apply:** when a prompt sentence names a letter for a case, grep the synthetic answers for that case first; the generator is the written convention's executable form.
