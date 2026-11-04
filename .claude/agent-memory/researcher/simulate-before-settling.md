---
name: simulate-before-settling
description: Patch final values offline (scripts/sim.py) and resolve before sending a round; partial outline fixes on page 38 moved entrances the wrong way
metadata:
  type: feedback
---

Before sending a settler round or changing outline code, simulate the outcome offline: `scripts/sim.py` patches the final values, assembles in place, resolves, prints reach and joins per level, and restores the reading.

**Why:** on 2026-11-03 three successive changes to page 38's outline code each moved outlines on four or more other lairs, and a partial fix to the crypts' strip lowered their reach (52 to 10 cells) before the full version reached 113 of 113. The simulator showed this in seconds at no cost.

**How to apply:** simulate every candidate fix and every case reading; diff the outlines against a saved reading (`reading-before-*.json`) and keep a change only when its diff is explained cell by cell. The simulator leaves out synthesized areas' edges, so a synthesized area reads as unreached there; trust `reach_all.py` on a combined key for those. See [[measure-joins-before-handover]].
