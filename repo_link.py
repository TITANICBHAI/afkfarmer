"""The GitHub repository link the run types into Replit's import page.

The link lives in ``repo.txt`` in the project folder. You can create that file
yourself, or let the run ask for the link in the terminal the first time and
write the file for you. It holds one link, for example::

    https://github.com/your-name/your-repo

Blank lines and lines starting with ``#`` are ignored, so notes are allowed.
Use another file name with ``REPO_FILE`` in config.py or, for one run, the
environment variable ``AFK_REPO_FILE`` (a relative name is taken from the
project folder).

Standard library only. Nothing here touches a website. A missing, empty or
damaged file simply means "no link yet": it never stops a run.
"""

from __future__ import annotations

import os
import re
import tempfile
from typing import Optional, Tuple
from urllib.parse import urlsplit

DEFAULT_FILE = "repo.txt"
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

_HOSTS = {"github.com", "www.github.com"}
_SEGMENT = re.compile(r"^[A-Za-z0-9_.-]+$")


def _configured_name() -> str:
    try:
        import config

        return str(getattr(config, "REPO_FILE", "") or "")
    except Exception:
        return ""


def path() -> str:
    """Absolute path of the link file."""

    name = os.environ.get("AFK_REPO_FILE") or _configured_name() or DEFAULT_FILE
    name = os.path.expanduser(name)
    return name if os.path.isabs(name) else os.path.join(PROJECT_DIR, name)


def normalize(text) -> Optional[str]:
    """The cleaned-up repository link, or None when ``text`` is not one.

    Accepts ``https://github.com/owner/repo`` (``http``, ``www``, a trailing
    ``/`` or ``.git`` and a deeper path such as ``/tree/main`` are fine) and
    the same without ``https://``. ``http`` becomes ``https``. Nothing else is
    guessed: ``owner/repo`` on its own, or a link to a profile, is refused.
    """

    value = str(text or "").strip().strip("\"'<>").strip()
    if not value or any(char.isspace() for char in value):
        return None
    if "://" not in value:
        value = "https://" + value
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").casefold()
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or host not in _HOSTS:
        return None
    if parts.username or parts.password:
        return None  # a token inside a link must never be typed into a page or saved
    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) < 2:
        return None
    if any(segment in (".", "..") or not _SEGMENT.match(segment) for segment in segments[:2]):
        return None
    if parts.scheme == "http":
        value = "https" + value[len("http"):]
    return value


def load() -> Tuple[Optional[str], str]:
    """Read the link file: ``(link, "")``, or ``(None, why)``.

    ``why`` is "missing" (no file), "empty" (nothing but blank lines and
    notes), "invalid" (the first line is not a GitHub repository link) or
    "unreadable" (the file cannot be opened).
    """

    try:
        with open(path(), "rb") as handle:
            raw = handle.read(65_536)  # a link is tiny; never read more
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "unreadable"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):  # Notepad's "Unicode" format
        text = raw.decode("utf-16", errors="replace")
    else:
        text = raw.decode("utf-8-sig", errors="replace")  # also drops a BOM
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line and not line.startswith("#")]
    if not lines:
        return None, "empty"
    link = normalize(lines[0])
    if link is None:
        return None, "invalid"
    return link, ""


def save(link) -> bool:
    """Write ``link`` to the file (creating it); False when it is not a link or cannot be saved."""

    clean = normalize(link)
    if clean is None:
        return False
    target = path()
    folder = os.path.dirname(target) or "."
    try:
        os.makedirs(folder, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".repo.", suffix=".tmp", dir=folder, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(clean + "\n")
            os.replace(temp_path, target)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
    except OSError:
        return False
    return True
