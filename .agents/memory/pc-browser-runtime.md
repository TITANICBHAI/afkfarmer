---
name: PC browser runtime
description: Replit workspace constraints discovered while running the live PC browser flow
---

The workspace Chromium process can launch Playwright only when the GCC-provided
library directory is included in `LD_LIBRARY_PATH`; the project wrapper handles
this explicitly. Microsoft Edge is not installed in the workspace, so browser
setup falls back to the workspace Chromium binary when Edge is unavailable.

**Why:** Python Playwright's `greenlet` extension could not load
`libstdc++.so.6` from the default shell environment, while the live
`temp-mail.org` page returned a Cloudflare block page even after the browser
started.

**How to apply:** Use the PC runtime wrapper for local browser startup. Treat a
provider block as a failed/manual-takeover state; do not bypass Cloudflare or
claim mailbox verification without an observed address and message.