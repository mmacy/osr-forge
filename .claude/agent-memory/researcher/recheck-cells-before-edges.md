---
name: recheck-cells-before-edges
description: A recheck that asks cells and edges in one round leaves edge answers resting on the old cell letters; follow it with an edge round that states the key's cells, and read every changed call's reason before sampling
metadata:
  type: feedback
---

After a recheck that can change cells, send every edge of a cell it turned to floor, and every changed edge between two floor cells, to the settler again with both cells' key letters and pixel counts (`recheck edges`). Then read the reason of every changed call before building the owner's sample, and ask again (`recheck reask`) any answer whose reason concludes another value.

**Why:** On 2026-11-03 a commercial map's diagonal recheck turned 9 cells to floor while, in the same round, the edges beside them were answered as if the cells were rock: an edge into a new floor cell kept a wall, an edge against a cell that had been rock was never settled at all (the edge round skips edges against rock), and one edge was called a wall because its neighbour, floor in the key, was taken for rock. The room the owner needed reachable stayed cut off until the edge round ran. Four of about 50 changed answers also contradicted their own reasons, such as a door answered `o` whose reason put the door on that edge.

**How to apply:** The settling order (cells, then edges with the settled cells applied) holds for a recheck too. Measure reachability or the owner's stated purpose after the recheck, not just the changed values; a changed cell is not done until its edges are.

**Wider form:** any step that turns a cell to floor after the edge round, whether a re-settling, a recheck, or the join round, leaves the edges between it and other floor unasked. The assembly then takes Claude's letter, or makes the edge a wall when Claude's read had rock on both sides. On the Chaotic Caves maps 67 such edges were found (`joins orphans`), and all were open; one of them was splitting orc lair A in two. Run `joins orphans` before measuring a key's joins.
