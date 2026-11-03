---
name: cave-mouths-before-resolving
description: Before resolving a cave page whose lairs are separate dungeons, check that their grounds are separate surfaces; read `mouths_closed` in assemble.log.json; the resolver can't cut between dungeons
metadata:
  type: feedback
---

Before resolving a page that holds several dungeons of the survey (ten lairs around a ravine), check from the drawing whether their walkable ground is one surface: print each floor cell's outline membership and look for floor that runs from one lair's rectangle into another's or into the ravine across open edges. Also read `mouths_closed` in `<map>/assemble.log.json`.

**Why:** On Chaotic Caves page 38 the lairs' white ground runs into the ravine across the map's elevation lines (never walls by the legend), so a walk from a lair's keys reaches the ravine without crossing a cave mouth; the cave-mouth rule never makes the ravine the outside, the lairs own the ravine, and the resolver lays passages between dungeons, which the passage model can't hold. Three hours went into the resolver's symptoms (an entrance key with no floor, a passage into rock, closed mouths) before the cause was measured: opening every mouth by hand left 71 of 2,024 floor cells outside, so the mouths weren't the cause. It is case 6 of the first answer keys.

**How to apply:** The measurement takes a minute: count floor cells per owner after the second partition (patch `Resolver.place_panels` to print them). When lairs share ground, stop and write the case; no settling round fixes a conventions gap. When the mouths are closed, the adversarial pass asks the calls around every mouth first (`settler._mouth_cells`).

**Outcome:** pull request 102 let an answer key's outline border follow an elevation line (each lair's slope round), and pull request 109 settled the rest: the outline round separates dungeons where the outlines made them meet (a level's outline holds its printed keys' cells, and borders cross at doors), and a cut between dungeons whose floors meet on the drawing lays passages that name the other dungeon. Measure which cut edges between dungeons come from the outlines and which from the drawing before proposing readings; on page 38, 22 of 25 came from the outlines.
