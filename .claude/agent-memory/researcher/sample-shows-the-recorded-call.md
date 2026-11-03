---
name: sample-shows-the-recorded-call
description: Before a sample goes to the owner, check each call's reason is the one for the value shown and that the call is one the key applies; a rebuilt readings table leaves stale label calls
metadata:
  type: feedback
---

Before handing the owner a sample, check two things on every call: the reason shown is the reason for the value shown (the re-settling's or the recheck's, not the first settler's), and the call is one the assembly applies. Label calls are keyed `m:<cell>|<index>` by their place in `items.json`, so rebuilding the table (a label re-read) leaves earlier label calls that name another label or none.

**Why:** On 2026-11-03 the maintainer marked a label call on a commercial map "unsure": the sample showed the value `none` beside the first settler's reason, which argued for a label, and the call itself was stale, since the label re-read had dropped that label from the table and the assembly never applied the call. His check of that call measured nothing about the key.

**How to apply:** `samples.sample_calls` now takes the reason of the recorded value and skips stale label calls; when a table is rebuilt mid-work, list the calls whose ids no longer match it before building any sample.
