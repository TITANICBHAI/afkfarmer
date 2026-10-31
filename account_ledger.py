"""A local list of the e-mail addresses this tool signed Replit accounts up with.

Why it exists: at the start of a run the browser may still be signed in to an
OLD Replit account, and the run deletes that account (Settings > Profile >
Request account deletion). The deletion no longer asks for a confirmation, so
this list is only a record: it lets the log say whether the account being
deleted is one this tool created.

Standard library only. The file is ``created_accounts.json`` in the working
folder (override with ``AFK_ACCOUNT_LEDGER``). Only addresses are stored, never
passwords. A damaged file counts as empty; it never stops a run.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import List, Optional

LEDGER_FILE = "created_accounts.json"


def _path() -> str:
    return os.environ.get("AFK_ACCOUNT_LEDGER") or LEDGER_FILE


def _clean(address: Optional[str]) -> str:
    return str(address or "").strip().casefold()


def load() -> List[str]:
    """Every recorded address (lower case); empty when the file is missing or damaged."""

    try:
        with open(_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        items = data.get("addresses", []) if isinstance(data, dict) else []
        if not isinstance(items, list):
            return []
        return [_clean(item) for item in items if isinstance(item, str) and _clean(item)]
    except (OSError, ValueError):
        return []


def is_known(address: Optional[str]) -> bool:
    wanted = _clean(address)
    return bool(wanted) and wanted in load()


def record(address: Optional[str]) -> bool:
    """Remember ``address``; returns False when it was empty or could not be saved."""

    wanted = _clean(address)
    if not wanted:
        return False
    known = load()
    if wanted in known:
        return True
    known.append(wanted)
    path = os.path.abspath(_path())
    try:
        fd, temp_path = tempfile.mkstemp(
            prefix=".ledger.", suffix=".tmp", dir=os.path.dirname(path) or ".", text=True
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"addresses": known}, handle, indent=2)
                handle.write("\n")
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
    except OSError:
        return False
    return True
