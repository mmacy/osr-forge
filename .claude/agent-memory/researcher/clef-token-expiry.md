---
name: clef-token-expiry
description: The wrangler OAuth token behind Clef expires after about an hour, and every failed request is still charged its estimate on the Clef ledger
metadata:
  type: feedback
---

Never run `wrangler` (not even `whoami`) from agent work: refreshing or changing the login writes the maintainer's credentials and is his action alone. When Clef answers 401, stop the Clef pass, record where it stopped, and go on with the rest. Make any bulk Clef script stop at the first `ClefAuthError`. The Workers AI account is on the free plan, whose daily allocation (10,000 neurons, about 1,250 Clef requests) a real-map pass exhausts in an hour; a pass over the answer-key maps needs the paid plan or the maintainer's decision.

**Why:** on 2026-11-02 the token expired an hour after the maintainer's login, in the middle of a 14,000-request pass; 3,400 requests failed with 401 and `ClefProvider` settled each at its estimate (about three times a real answer's cost), so the Clef ledger charged about $0.63 for nothing before the run was stopped by hand. The lead then ruled that running `wrangler` to refresh the token is the maintainer's action, not an agent's, and the daily free allocation ran out minutes after the refresh anyway.

**How to apply:** before a Clef pass of more than a few hundred requests, confirm with the lead that the login is fresh and the plan allows the volume; stop on auth errors and on 429; budget for the estimate-priced failures the provider's policy charges.
