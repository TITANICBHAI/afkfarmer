"""Pure helpers for classifying visible Replit registration states."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit


CAPTCHA_PATTERNS = (
    re.compile(r"\bcaptcha\b", re.IGNORECASE),
    re.compile(r"\brecaptcha\b", re.IGNORECASE),
    re.compile(r"\bi['’]?m not a robot\b", re.IGNORECASE),
    re.compile(r"\bverify you are human\b", re.IGNORECASE),
)

VALIDATION_PATTERNS = (
    re.compile(r"\binvalid email\b", re.IGNORECASE),
    re.compile(r"\bemail(?: address)?\s+(?:is\s+)?required\b", re.IGNORECASE),
    re.compile(r"\bpassword\s+(?:is\s+)?required\b", re.IGNORECASE),
    re.compile(r"\bpassword\b.{0,40}\b(?:8|characters|long)\b", re.IGNORECASE),
    re.compile(r"\balready exists\b", re.IGNORECASE),
    re.compile(r"\binvalid (?:email|password|credentials)\b", re.IGNORECASE),
    re.compile(r"\bplease enter\b", re.IGNORECASE),
    re.compile(r"\bsomething went wrong\b", re.IGNORECASE),
)

SUCCESS_PATTERNS = (
    re.compile(r"\bcheck your (?:email|inbox)\b", re.IGNORECASE),
    re.compile(r"\bverification email\b", re.IGNORECASE),
    re.compile(r"\bverify your email\b", re.IGNORECASE),
    re.compile(r"\baccount created\b", re.IGNORECASE),
)

AUTHENTICATED_PATTERNS = (
    re.compile(r"\bpersonal workspace\b", re.IGNORECASE),
    re.compile(r"\bwhat are we working on today\b", re.IGNORECASE),
    re.compile(r"\bmy repls\b", re.IGNORECASE),
)


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def has_captcha_text(value: Any) -> bool:
    """Return whether visible text contains a manual CAPTCHA challenge."""

    text = _text(value)
    return any(pattern.search(text) for pattern in CAPTCHA_PATTERNS)


def find_validation_errors(value: Any) -> list[str]:
    """Return unique visible lines that look like registration errors."""

    errors: list[str] = []
    for raw_line in str(value or "").splitlines():
        line = _text(raw_line)
        if line and any(pattern.search(line) for pattern in VALIDATION_PATTERNS):
            if line not in errors:
                errors.append(line)
    return errors


def _url_changed(current_url: Any, signup_url: Any) -> bool:
    try:
        current = urlsplit(str(current_url or ""))
        signup = urlsplit(str(signup_url or ""))
    except ValueError:
        return False
    return (current.scheme, current.netloc, current.path) != (
        signup.scheme,
        signup.netloc,
        signup.path,
    )


def classify_signup_state(
    current_url: Any,
    page_text: Any,
    signup_url: str = "https://replit.com/signup",
    baseline_url: Any = None,
) -> str:
    """Classify a visible registration result as waiting, error, or submitted."""

    if has_captcha_text(page_text):
        return "captcha"
    if find_validation_errors(page_text):
        return "validation"
    if any(pattern.search(str(page_text or "")) for pattern in SUCCESS_PATTERNS):
        return "submitted"
    if _url_changed(current_url, baseline_url if baseline_url is not None else signup_url):
        return "submitted"
    return "waiting"


def classify_session_state(current_url: Any, page_text: Any) -> str:
    """Classify the visible post-mobile browser state."""

    text = str(page_text or "")
    if any(pattern.search(text) for pattern in AUTHENTICATED_PATTERNS):
        return "authenticated"

    try:
        path = urlsplit(str(current_url or "")).path.casefold()
    except ValueError:
        path = ""
    if path.rstrip("/").endswith("/login"):
        return "login_required"
    return "unknown"