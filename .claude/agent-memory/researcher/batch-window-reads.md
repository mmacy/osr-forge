---
name: batch-window-reads
description: What Sonnet 5.5 window reads through the Batches API cost and how often they come back malformed, measured on the first answer keys
metadata:
  type: project
---

A 12 by 12 window read on claude-sonnet-5-5 at high effort through the Batches API costs $0.012 to $0.022 (2,000 to 4,000 output tokens, ~2,100 input, system prompt cached), under half the plan's $0.038 estimate. In the first batch of 8 synthetic windows, 4 answers were malformed: a stray comma in a row, letters `e` and `r` in an edge row, a row one letter short, and two answers that stopped after a few rows with `end_turn`. The retry (request + malformed answer + faults) fixed the crypt's; the manor's retries took over 10 minutes. A batch of 8 returned in about 6 minutes.

**Why:** the answer-key budget and the schedule depend on these figures, and the plan's estimates were from single Messages API calls.

**How to apply:** reserve the worst case but expect about $0.02 a window; always queue a retry and a fresh attempt; start the next stage's work while a batch runs instead of polling in the foreground.
