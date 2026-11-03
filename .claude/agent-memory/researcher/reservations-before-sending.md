---
name: reservations-before-sending
description: Sum every batch's worst case against the settled spend before sending, because the budget counts reservations in flight
metadata:
  type: feedback
---

Before sending a batch, compute settled spend plus every reservation in flight plus the new batch's worst case, and send only when that stays under the budget; when several maps' rounds are ready at once, send them one map at a time as the earlier ones settle.

**Why:** on 2026-11-02 six adversarial rounds and a label re-read went out together and put the Claude ledger at $48.88 for this work ($23.30 settled, $25.55 reserved) against a $45 line that counts reservations; the batches settled to about a tenth of their worst case within the hour, but the line was crossed, and the lead had set it in plain words.

**How to apply:** the worst case of a settling or adversarial request is about $0.11 and of a window read about $0.13; a round of N requests reserves about N times that until its answers come back.
