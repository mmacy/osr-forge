---
name: batch-counts-lag
description: A Batches API batch's request_counts can show every request still processing hours after most have answers; cancelling ends it at once and returns the finished answers
metadata:
  type: reference
---

A batch's `request_counts` and `processing_status` can lag far behind its results. On 2026-11-03 a 58-request recheck batch showed `processing=58, succeeded=0` for two and a half hours while batches sent an hour later had ended; cancelling it returned 54 succeeded answers and 4 cancelled requests, and the cancelled ones cost nothing and were sent again.

**How to apply:** when a batch shows no progress for over an hour while later batches of the same size have ended, cancel it, collect it (the store settles the cancelled reservations at nothing), and resend only the jobs left without an answer, by id. Don't wait out the 24-hour window.
