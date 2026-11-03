---
name: resend-only-retries
description: A collect's resend must name the retry jobs it queued; a store-wide submit after a collect sends every job waiting for a later part and reserves a whole pass at once
metadata:
  type: feedback
---

When a collect queues retries, resend those jobs by id (`store.submit(..., ids=store.added)` or the collect's `retry_ids`), never the store's whole pending list.

**Why:** Page 38's adversarial pass was sent in parts under `--jobs`, with 320 jobs waiting in the store for later parts. The first part's collect found one malformed answer and called the store's submit with no bound, which sent all 320 as "retries" with $36.61 reserved and put the work at $65.81 against a $45 line. The batches were cancelled within a minute, so two requests were charged, but the breach was real.

**How to apply:** Any pass sent in parts leaves pending jobs in its store, and every code path that submits after a collect must be bounded by ids. Before sending a pass in parts, read every resend call in the pipeline and check its bound.
