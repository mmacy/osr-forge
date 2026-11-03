---
name: batch-store-sweep
description: Before reporting spend, collect every batch store so no reservation stays open; a batch superseded by a later attempt is never collected by the stage that moved on
metadata:
  type: feedback
---

Before reporting spend, sweep every batch store (`windows`, `marks`, `wholemap`, `settle`, `adversary`, `resettle`, `recheck` under each map) with `BatchStore.collect` and confirm the ledger shows no `pending` entry for the work.

**Why:** A window retry batch of one job stayed uncollected for three hours because the window had been settled from a later attempt and no stage ever collected the old batch; its $0.135 reservation sat in the ledger as spend in flight until a sweep found it.

**How to apply:** Reservations count against the budget line. A stage that sends a fresh attempt while an older batch of the same item is still out must still collect the older batch, so run a sweep of all stores before each spend figure in a progress entry and before the final report.
