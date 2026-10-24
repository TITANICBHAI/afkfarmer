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

SECURITY_CHALLENGE_PATTERNS = CAPTCHA_PATTERNS + (
    re.compile(r"\bsuspicious(?:\s+login|\s+activity)?\b", re.IGNORECASE),
    re.compile(r"\baccount\s+(?:restricted|locked|suspended)\b", re.IGNORECASE),
    re.compile(r"\brate\s*limit(?:ed)?\b", re.IGNORECASE),
    re.compile(r"\badditional\s+(?:identity|security)\s+verification\b", re.IGNORECASE),
    re.compile(r"\bsecurity\s+verification\b", re.IGNORECASE),
)

# Replit (and any site using Google reCAPTCHA Enterprise) keeps a legal notice in
# every sign-up and log-in modal: "This site is protected by reCAPTCHA
# Enterprise and the Google Privacy Policy and Terms of Service apply."
# (see attached_assets/step_2.3, 3.2 and 4.png). It is a permanent disclosure,
# not a challenge, so it must not be classified as one. The pattern only
# covers that fixed vocabulary; any other text mentioning reCAPTCHA or CAPTCHA
# (for example "Please complete the CAPTCHA") is still a challenge.
RECAPTCHA_NOTICE_PATTERN = re.compile(
    r"(?:\b(?:this\s+)?(?:site|page|website)\s+is\s+)?"
    r"protected\s+by\s+(?:google\s+)?re-?captcha(?:\s+(?:enterprise|v[23]))?"
    r"(?:\s+and\s+the\s+google)?"
    r"(?:\s+privacy\s+policy)?"
    r"(?:\s+and)?"
    r"(?:\s+terms(?:\s+of\s+service)?)?"
    r"(?:\s+apply)?\.?",
    re.IGNORECASE,
)

VALIDATION_PATTERNS = (
    re.compile(r"\binvalid email\b", re.IGNORECASE),
    re.compile(r"\bemail(?: address)?\s+(?:is\s+)?required\b", re.IGNORECASE),
    re.compile(r"\bpassword\s+(?:is\s+)?required\b", re.IGNORECASE),
    re.compile(r"\bpassword\b.{0,40}\b(?:8|characters|long)\b", re.IGNORECASE),
    re.compile(
        r"\bpassword\b.{0,40}\b(?:too\s+(?:short|weak|common)|at\s+least)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\balready\s+(?:exists|in\s+use|taken|registered)\b", re.IGNORECASE),
    re.compile(r"\binvalid (?:email|password|credentials)\b", re.IGNORECASE),
    re.compile(r"\bplease enter\b", re.IGNORECASE),
    re.compile(r"\bsomething went wrong\b", re.IGNORECASE),
)

# Positive messages Replit shows under the filled-in fields once its
# asynchronous checks finish (attached_assets/3.2, 3.3).
FORM_VALID_PATTERNS = (
    re.compile(r"\bemail\s+is\s+available\b", re.IGNORECASE),
    re.compile(r"\bpassword\s+is\s+valid\b", re.IGNORECASE),
)

SUCCESS_PATTERNS = (
    re.compile(r"\bcheck your (?:email|inbox)\b", re.IGNORECASE),
    re.compile(r"\bverification email\b", re.IGNORECASE),
    re.compile(r"\bverify your email\b", re.IGNORECASE),
    re.compile(r"\bemail was sent to\b", re.IGNORECASE),
    re.compile(r"\baccount created\b", re.IGNORECASE),
)

AUTHENTICATED_PATTERNS = (
    re.compile(r"\bpersonal workspace\b", re.IGNORECASE),
    re.compile(r"\bwhat are we working on today\b", re.IGNORECASE),
    re.compile(r"\bmy repls\b", re.IGNORECASE),
)


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def strip_recaptcha_notice(value: Any) -> str:
    """Return normalized text without the permanent reCAPTCHA legal notice."""

    return _text(RECAPTCHA_NOTICE_PATTERN.sub(" ", _text(value)))


def has_captcha_text(value: Any) -> bool:
    """Return whether visible text contains a manual CAPTCHA challenge."""

    text = strip_recaptcha_notice(value)
    return any(pattern.search(text) for pattern in CAPTCHA_PATTERNS)


def has_security_challenge_text(value: Any) -> bool:
    """Return whether visible text requires manual security intervention."""

    text = strip_recaptcha_notice(value)
    return any(pattern.search(text) for pattern in SECURITY_CHALLENGE_PATTERNS)


def find_validation_errors(value: Any) -> list[str]:
    """Return unique visible lines that look like registration errors."""

    errors: list[str] = []
    for raw_line in str(value or "").splitlines():
        line = _text(raw_line)
        if line and any(pattern.search(line) for pattern in VALIDATION_PATTERNS):
            if line not in errors:
                errors.append(line)
    return errors


def classify_signup_form_text(value: Any) -> str:
    """Classify the filled-in credentials form as valid, invalid or pending.

    Replit validates both fields asynchronously (a spinner next to each field,
    then "Email is available" / "Password is valid"). Submitting before those
    messages appear races the checks, so the caller waits for ``valid``.
    Only both positive messages count as valid; an error line wins otherwise.
    """

    text = _text(value)
    if all(pattern.search(text) for pattern in FORM_VALID_PATTERNS):
        return "valid"
    if find_validation_errors(value):
        return "invalid"
    return "pending"


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

    if has_security_challenge_text(page_text):
        return "captcha" if has_captcha_text(page_text) else "security_challenge"
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

WORKSPACE_PATH_PATTERN = re.compile(r"^/~(?:/|$)")
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
HANDLE_PATTERN = re.compile(r"(?<![\w.@])@([A-Za-z0-9_]{3,})")


def is_workspace_url(current_url: Any) -> bool:
    """Whether the URL is the signed-in workspace home (``https://replit.com/~``).

    The character is a tilde, not a hyphen. Replit sends a signed-in visitor of
    ``replit.com`` here, so seeing it right after opening Replit means an old
    login is still in the browser.
    """

    try:
        parts = urlsplit(str(current_url or ""))
    except ValueError:
        return False
    host = (parts.hostname or "").casefold()
    on_replit = host == "replit.com" or host.endswith(".replit.com")
    return on_replit and bool(WORKSPACE_PATH_PATTERN.match(parts.path))


def parse_account_identity(page_text: Any) -> dict:
    """The e-mail and @username shown on Replit's Settings > Profile page.

    Returns ``{"email": str | None, "username": str | None}``. The e-mail is the
    address under "Your email"; the username is the first ``@handle`` on the page.
    """

    text = str(page_text or "")
    email = None
    labelled = re.search(r"your\s+e-?mail", text, re.IGNORECASE)
    if labelled:
        found = EMAIL_PATTERN.search(text, labelled.end())
        email = found.group(0) if found else None
    handle = HANDLE_PATTERN.search(text)
    return {"email": email, "username": handle.group(1) if handle else None}


def is_profile_settings_text(page_text: Any) -> bool:
    """Whether text is the signed-in Settings > Profile page (it has the delete control)."""

    text = _text(page_text).casefold()
    return "request account deletion" in text or (
        "your email" in text and "first name" in text
    )


def classify_replit_page(
    current_url: Any,
    page_text: Any,
    *,
    fields: bool = False,
    chooser: bool = False,
    challenge: bool = False,
    create_button: bool = False,
) -> str:
    """Name the point of the Replit web flow a page is at (no navigation needed).

    The booleans are what the caller saw on the page: the credentials fields
    (``fields``), the "Continue with Email" chooser (``chooser``), a visible
    challenge widget (``challenge``) and a "Create account" button
    (``create_button``). Returns one of: ``challenge``, ``authenticated``,
    ``check_email``, ``login``, ``signup_form``, ``signup_chooser``,
    ``landing``, ``other``.
    """

    if challenge or has_security_challenge_text(page_text):
        return "challenge"
    if classify_session_state(current_url, page_text) == "authenticated":
        return "authenticated"
    if any(pattern.search(str(page_text or "")) for pattern in SUCCESS_PATTERNS):
        return "check_email"
    # The workspace URL alone (the page text may still be loading) counts as a
    # login too, but only after the "check your email" evidence above, so the
    # account that is being created right now is never mistaken for an old one,
    # and only while the page shows no logged-out controls.
    if is_workspace_url(current_url) and not (fields or chooser or create_button):
        return "authenticated"
    try:
        parts = urlsplit(str(current_url or ""))
        host, path = (parts.hostname or "").casefold(), parts.path.casefold().rstrip("/")
    except ValueError:
        host, path = "", ""
    on_replit = host == "replit.com" or host.endswith(".replit.com")
    if fields:
        return "login" if path.endswith("/login") else "signup_form"
    if path.endswith("/login") and on_replit:
        return "login"
    if chooser:
        return "signup_chooser"
    if on_replit and (create_button or path in {"", "/signup"}):
        return "landing"
    return "other"
