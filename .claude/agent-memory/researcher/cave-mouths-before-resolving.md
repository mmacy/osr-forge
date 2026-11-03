---
name: cave-mouths-before-resolving
description: On a cave map, read `mouths_closed` in assemble.log.json before resolving; entrance letters printed in a mouth read as rock and wall, and closed mouths give the ravine to the lairs
metadata:
  type: feedback
---

After assembling a cave-style map, read `mouths_closed` in `<map>/assemble.log.json` before running the resolver, and send the calls around every cave mouth through the settler or the adversarial pass before trusting the key.

**Why:** On Chaotic Caves page 38, both readers and Clef read the heavy lair letters and arrows printed in the cave mouths as rock and walls, so fourteen of fifteen mouths were closed. Walks from the lairs' keys then reached the ravine through other open edges without crossing a mouth, the cave-mouth rule never made the ravine the outside, the lairs owned the ravine between them, and the resolver laid passages between dungeons, which the passage model can't hold. Two hours went into diagnosing it from the resolver's side (an entrance key with no floor, then a passage into rock) before the mouths were looked at.

**How to apply:** A mouth is an outer opening: its edge is `o` and both cells are floor unless a door is drawn. The adversarial pass now always asks the cells and edges within one cell of every mouth first (`settler._mouth_cells`). When a key resolves with cross-level passages inside one page's cave system, look for closed mouths before looking for a rule gap.
