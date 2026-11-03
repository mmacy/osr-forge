---
name: measure-joins-before-handover
description: Window scores don't measure connection; before handing over an answer key, count the prose joins it makes and the reach from each entrance, per level
metadata:
  type: feedback
---

Before reporting an answer key as done, run `assemble.prose_joins` (it is in every key's provenance) and a reach walk from each dungeon's entrance (`~/.re-pod/map-plan/osr-suite/answer-keys/scripts/reach_all.py`). A key can score 97% of cells right on its settled windows and still split cave levels apart, because a narrow or diagonal passage read as rock or wall breaks a join that no window covers.

**Why:** On 2026-11-03 the Chaotic Caves key made 87 of 128 prose joins (page 38's lairs 39 of 70; orc lair A 3 of 11) while its windows scored 138 to 143 of 144 cells. The coordinator called the join check "exactly what was needed", and the join round that followed brought orc A to 11 of 11.

**How to apply:** The join round (`pipeline ... joins measure|cells|edges|check|resettle|final`) asks each gap's cells, then their edges with the key's cell letters, then checks and re-settles. Three rounds reached the point where most remaining gap cells were 0% floor. On a 5-foot map the resolver's grid is converted to 10-foot cells, so gap coordinates must map back to the reading's squares. After any re-settling, ask again any wall it put between two cells the key has as floor.
