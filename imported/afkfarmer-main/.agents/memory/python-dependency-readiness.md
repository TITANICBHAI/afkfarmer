---
name: Python dependency readiness
description: Environment-specific guidance for validating this repository's Python prerequisites.
---

The repository's pinned Python packages can be absent even when an earlier tracker entry says they were installed. Restore them through the managed package workflow and rerun `python preflight.py` before relying on import or browser-flow checks.

**Why:** A later session showed tracker history and the active Python environment had diverged; preflight exposed the mismatch before any external automation was started.

**How to apply:** Treat a passing preflight as current evidence for dependencies, and keep live browser/device verification separate from local package readiness.