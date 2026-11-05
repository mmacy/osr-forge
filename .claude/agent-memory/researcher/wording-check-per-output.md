---
name: wording-check-per-output
description: When a convention's wording changes, test the implementation with an independent per-output check of the wording's condition and an old-versus-new diff, then look at each change in a gridded crop
metadata:
  type: feedback
---

When a convention's wording changes, check the implementation in three steps before settling or rebuilding a key:

1. Write a check that tests the wording's condition on every output, without calling the code under test to decide it. For the passage rule, `scripts/chains6.py` lists each kept cell that has a cell with more floor across its course.
2. Diff the old outputs against the new ones (`scripts/chains5.py` with the old module loaded as `kept_old`).
3. Look at each changed output in a gridded crop that shows shares (`scripts/grid_crop.py`).

**Why:** on 2026-11-04 rereading `kept.py` against the merged wording found nothing. The per-output check found a chain forced off the cell with more floor. The diff and crops then showed two defects that the reread could not show: title-block paper counted as floor, and a grid line letting the paper's closing cross rock.

**How to apply:** do this whenever the coordinator says "check X against the new wording". Send close calls to the settler only once the diff holds no defect, so the settler isn't asked about choices that a fix would remove. Related: [[simulate-before-settling]], [[measure-joins-before-handover]].
