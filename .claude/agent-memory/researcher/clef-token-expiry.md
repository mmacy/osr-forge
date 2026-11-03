---
name: clef-token-expiry
description: The wrangler OAuth token behind Clef expires after about an hour, and every failed request is still charged its estimate on the Clef ledger
metadata:
  type: feedback
---

Refresh the wrangler token with `wrangler whoami` (on PATH under `~/.nvm/versions/node/*/bin`) before each long Clef run, and make any bulk Clef script stop at the first `ClefAuthError`.

**Why:** on 2026-11-02 the token expired an hour after the maintainer's login, in the middle of a 14,000-request pass; 3,400 requests failed with 401 and `ClefProvider` settled each at its estimate (about three times a real answer's cost), so the Clef ledger charged about $0.63 for nothing before the run was stopped by hand. `wrangler whoami` renews the token from its refresh token without a browser.

**How to apply:** any script that sends more than a few hundred Clef requests: refresh first, stop on auth errors, and budget for the estimate-priced failures the provider's policy charges.
