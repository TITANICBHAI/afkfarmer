"""Browser-backed temporary-mail provider primitives.

This module does not open a browser on import. A provider only contacts the
configured site when ``obtain_address`` is called by the orchestrator.
"""

from __future__ import annotations

import re
import time
from html import unescape
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from email_provider import (
    EmailProvider,
    EmailProviderError,
    verification_success_observed,
)


EMAIL_PATTERN = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    flags=re.IGNORECASE,
)
PROVIDER_BLOCK_MARKERS = (
    "sorry, you have been blocked",
    "you are unable to access temp-mail.org",
    "attention required",
    "cloudflare ray id",
)
VERIFICATION_URL_PATTERN = re.compile(
    r"https://(?:[A-Z0-9-]+\.)*replit\.com/action-code(?:[/?#][^\s\"'<>)]*)?",
    flags=re.IGNORECASE,
)

# Inbox rows are recognised by their content, never by class names: the sender
# address and a subject mentioning replit / verify / mail inside one short
# element. The sender address is removed before the subject is judged, because
# "verify@replit.com" itself contains "verify" and "replit".
ROW_SENDER_PATTERN = r"\bverify@replit\.com\b"
ROW_SUBJECT_PATTERNS = (r"replit", r"verify", r"mail")
SUBJECT_TEXT_PATTERN = re.compile(r"verify\s+your\s+e-?mail", re.IGNORECASE)
# Text that exists only once the message itself is open. The list row's
# subject ("Replit: Verify Your Email") must not satisfy it.
MESSAGE_OPEN_PATTERN = re.compile(
    r"verify\s+now|verify\s+email\b|action-code",
    re.IGNORECASE,
)
# Returns the innermost rendered element whose short text holds both the sender
# and a verification-looking subject (the JavaScript twin of
# ``is_verification_row_text``), or null.
VERIFICATION_ROW_JS = r"""([senderSource, subjectSources]) => {
    const sender = new RegExp(senderSource, 'i');
    const senderAll = new RegExp(senderSource, 'ig');
    const subjects = subjectSources.map(source => new RegExp(source, 'i'));
    const rendered = element => {
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return rect.width > 0 && rect.height > 0 &&
               style.visibility !== 'hidden' && style.display !== 'none';
    };
    const hits = [];
    for (const element of document.body.querySelectorAll('*')) {
        if (!rendered(element)) continue;
        const text = (element.innerText || '').replace(/\s+/g, ' ');
        if (!text || text.length > 400 || !sender.test(text)) continue;
        const subject = text.replace(senderAll, ' ');
        if (subjects.every(pattern => pattern.test(subject))) hits.push(element);
    }
    const innermost = hits.filter(
        element => !hits.some(other => other !== element && element.contains(other))
    );
    return innermost.length ? innermost[0] : null;
}"""


def is_verification_row_text(value: Any) -> bool:
    """Return whether one inbox row's text is the Replit verification message."""

    text = " ".join(str(value or "").split())
    if not re.search(ROW_SENDER_PATTERN, text, flags=re.IGNORECASE):
        return False
    subject = re.sub(ROW_SENDER_PATTERN, " ", text, flags=re.IGNORECASE)
    return all(
        re.search(pattern, subject, flags=re.IGNORECASE)
        for pattern in ROW_SUBJECT_PATTERNS
    )


TempMailError = EmailProviderError

def normalize_email(value: Any) -> str:
    """Return a normalized candidate email address or an empty string."""

    if value is None:
        return ""
    return str(value).replace("\u200b", "").strip().lower()


def provider_block_reason(value: Any) -> Optional[str]:
    """Return a stable reason when the provider blocks the browser/IP."""

    text = " ".join(str(value or "").casefold().split())
    if any(marker in text for marker in PROVIDER_BLOCK_MARKERS):
        return "provider_blocked"
    return None


def extract_email_candidates(values: list[Any]) -> list[str]:
    """Extract unique email candidates while preserving discovery order."""

    candidates: list[str] = []
    for value in values:
        for match in EMAIL_PATTERN.findall(normalize_email(value)):
            candidate = normalize_email(match)
            if candidate and candidate not in candidates:
                candidates.append(candidate)
    return candidates


def is_replit_verification_message(sender: Any, subject: Any) -> bool:
    """Return whether sender and subject identify the requested Replit mail."""

    sender_text = normalize_email(sender)
    subject_text = " ".join(str(subject or "").split()).casefold()
    return (
        bool(re.search(r"\bverify@replit\.com\b", sender_text, flags=re.IGNORECASE))
        and "replit" in subject_text
        and "verify" in subject_text
        and ("email" in subject_text or "mail" in subject_text)
    )


def extract_verification_url(values: list[Any]) -> Optional[str]:
    """Extract the first expected Replit verification URL from message content."""

    for value in values:
        for match in VERIFICATION_URL_PATTERN.findall(unescape(str(value or ""))):
            if is_expected_verification_url(match):
                return match
    return None


def is_expected_verification_url(value: Any) -> bool:
    """Validate a visible verification URL before it is followed."""

    try:
        parsed = urlsplit(str(value or "").strip())
        port = parsed.port
    except ValueError:
        return False
    hostname = (parsed.hostname or "").casefold()
    path = parsed.path.casefold()
    return (
        parsed.scheme.casefold() == "https"
        and not parsed.username
        and not parsed.password
        and port is None
        and (hostname == "replit.com" or hostname.endswith(".replit.com"))
        and (path == "/action-code" or path.startswith("/action-code/"))
    )


TempMailProvider = EmailProvider


class TempMailOrgProvider(EmailProvider):
    """Read and verify the visible mailbox address from temp-mail.org."""

    URL = "https://temp-mail.org/"
    provider_name = "temp-mail.org"

    ADDRESS_SELECTORS = (
        "input#mail",
        "input[name='mail']",
        "input[name*='mail' i]",
        "input[id*='mail' i]",
        "input[aria-label*='mail' i]",
        "[data-testid*='mail' i]",
        "[data-testid*='email' i]",
        "[class*='mail' i]",
        "[class*='email' i]",
    )

    COPY_SELECTORS = (
        "button:has-text('Copy')",
        "[role='button']:has-text('Copy')",
        "button[aria-label*='copy' i]",
        "[role='button'][aria-label*='copy' i]",
        "[title*='copy' i]",
        "[data-testid*='copy' i]",
        "[class*='copy' i]",
    )
    REFRESH_SELECTORS = (
        "button:has-text('Refresh')",
        "[role='button']:has-text('Refresh')",
        "a:has-text('Refresh')",
    )
    DELETE_SELECTORS = (
        "button:has-text('Delete')",
        "[role='button']:has-text('Delete')",
        "a:has-text('Delete')",
        "[aria-label*='delete' i]",
    )
    # A confirmation, if the site asks for one; looked for inside dialogs only.
    DELETE_CONFIRM_SELECTORS = (
        "[role='dialog'] button:has-text('Delete')",
        "[role='dialog'] button:has-text('Yes')",
        "[role='dialog'] button:has-text('OK')",
        "[role='dialog'] button:has-text('Confirm')",
        ".modal button:has-text('Delete')",
        ".modal button:has-text('Yes')",
        ".modal button:has-text('OK')",
    )
    DELETE_WAIT_MS = 8_000
    REFRESH_INTERVAL_SECONDS = 8
    OPEN_CLICK_TIMEOUT_MS = 2_500   # one checked click on an inbox listing
    LOCATE_BOX_TIMEOUT_MS = 2_000   # reading where a listing is, for the mouse click
    VERIFY_EMAIL_SELECTORS = (
        "a:has-text('Verify Email')",
        "button:has-text('Verify Email')",
        "[role='button']:has-text('Verify Email')",
        "[aria-label*='verify email' i]",
    )
    VERIFY_NOW_SELECTORS = (
        "a:has-text('Verify Now')",
        "button:has-text('Verify Now')",
        "[role='button']:has-text('Verify Now')",
        "[aria-label*='verify now' i]",
    )

    def __init__(
        self,
        context: Any,
        url: str = URL,
        timeout_ms: int = 30_000,
    ) -> None:
        self.context = context
        self.url = url
        self.timeout_ms = timeout_ms
        self.page: Optional[Any] = None
        self.address: Optional[str] = None

    def open(self) -> Any:
        """Open the provider page in the shared browser context."""

        if self.page is None or self.page.is_closed():
            existing_pages = []
            try:
                existing_pages = [
                    page for page in self.context.pages if not page.is_closed()
                ]
            except Exception:
                pass

            # Prefer the mailbox tab the operator already opened. If it is not
            # present, reuse a blank tab before creating a new tab in the same
            # browser context. This never launches another browser instance.
            self.page = next(
                (
                    page
                    for page in existing_pages
                    if "temp-mail.org" in page.url.casefold()
                ),
                None,
            )
            if self.page is None:
                self.page = next(
                    (
                        page
                        for page in existing_pages
                        if page.url in {"", "about:blank", "chrome://newtab/"}
                    ),
                    None,
                )
            if self.page is None:
                self.page = self.context.new_page()
        try:
            self.context.grant_permissions(
                ["clipboard-read", "clipboard-write"],
                origin=self.url.rstrip("/"),
            )
        except Exception:
            # Clipboard permission is browser-dependent. Copy verification
            # still requires either clipboard text or a visible confirmation.
            pass

        self._to_front()
        self.page.goto(self.url, wait_until="domcontentloaded")
        self.page.wait_for_load_state("domcontentloaded")
        return self.page

    def _to_front(self) -> None:
        """Make the mailbox the active tab before it is used.

        A browser draws, and lets a page animate, only the tab that is in front.
        In any other tab Playwright's "wait until the element is stable" before
        a click can stall for seconds or never finish, and a click on an inbox
        that redraws itself then lands on an element that is already gone.
        """

        try:
            self.page.bring_to_front()
        except Exception:
            pass  # not every page object or browser supports it; never fatal

    def _read_dom_values(self) -> list[Any]:
        """Collect likely address values without assuming one page layout."""

        values: list[Any] = []
        for selector in self.ADDRESS_SELECTORS:
            locator = self.page.locator(selector)
            try:
                if locator.count():
                    values.extend(
                        locator.evaluate_all(
                            """elements => elements.flatMap(element => [
                                element.value,
                                element.textContent,
                                element.getAttribute('aria-label'),
                                element.getAttribute('title')
                            ])"""
                        )
                    )
            except Exception:
                continue

        try:
            values.append(self.page.locator("body").inner_text())
        except Exception:
            pass
        return values

    def read_address(self) -> str:
        """Wait until a real email address is visible and return it."""

        if self.page is None:
            self.open()

        try:
            block_reason = provider_block_reason(self.page.locator("body").inner_text())
        except Exception:
            block_reason = None
        if block_reason:
            self._save_failure(block_reason)
            raise TempMailError(
                "Temporary-mail provider blocked this browser or IP; "
                "manual takeover or a permitted provider-side resolution is required."
            )

        try:
            self.page.wait_for_function(
                """() => {
                    const nodes = Array.from(document.querySelectorAll(
                        "input, textarea, [contenteditable='true'], body"
                    ));
                    return nodes.some(node => {
                        const value = node.value || node.innerText || node.textContent || "";
                        return /[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}/i.test(value);
                    });
                }""",
                timeout=self.timeout_ms,
            )
        except Exception as exc:
            try:
                block_reason = provider_block_reason(
                    self.page.locator("body").inner_text()
                )
            except Exception:
                block_reason = None
            if block_reason:
                self._save_failure(block_reason)
                raise TempMailError(
                    "Temporary-mail provider blocked this browser or IP; "
                    "manual takeover or a permitted provider-side resolution is required."
                ) from exc
            self._save_failure("address_loading")
            raise TempMailError(
                "Temporary-mail address did not leave the loading state."
            ) from exc

        candidates = extract_email_candidates(self._read_dom_values())
        if not candidates:
            self._save_failure("address_not_found")
            raise TempMailError("No mailbox address was found in the page UI.")

        self.address = candidates[0]
        return self.address

    def _copy_button(self) -> Any:
        for selector in self.COPY_SELECTORS:
            locator = self.page.locator(selector)
            try:
                for index in range(locator.count()):
                    candidate = locator.nth(index)
                    if candidate.is_visible(timeout=500):
                        return candidate
            except Exception:
                continue
        return None

    def _clipboard_value(self) -> str:
        try:
            value = self.page.evaluate("navigator.clipboard.readText()")
        except Exception:
            return ""
        return normalize_email(value)

    def _copy_feedback_visible(self) -> bool:
        for text in ("Copied", "Copied!"):
            locator = self.page.get_by_text(text, exact=False)
            try:
                if locator.is_visible(timeout=1_000):
                    return True
            except Exception:
                continue
        return False

    def copy_address(self) -> str:
        """Click Copy and prove that the requested address was copied."""

        if not self.address:
            self.read_address()
        button = self._copy_button()
        if button is None:
            self._save_failure("copy_button_not_found")
            raise TempMailError("The mailbox Copy control was not found.")

        try:
            button.click()
        except Exception as exc:
            self._save_failure("copy_click_failed")
            raise TempMailError("The mailbox Copy control could not be clicked.") from exc

        copied = self._clipboard_value()
        if copied == normalize_email(self.address) or self._copy_feedback_visible():
            return self.address

        self._save_failure("copy_not_verified")
        raise TempMailError(
            "Copy was clicked, but neither clipboard text nor visible copy "
            "confirmation matched the mailbox address."
        )

    def obtain_address(self) -> str:
        self.open()
        self.read_address()
        return self.copy_address()

    def _visible_control(self, selectors: tuple[str, ...]) -> Any:
        for selector in selectors:
            locator = self.page.locator(selector)
            try:
                for index in range(locator.count()):
                    candidate = locator.nth(index)
                    if candidate.is_visible(timeout=500):
                        return candidate
            except Exception:
                continue
        return None

    def _pause(self, milliseconds: int) -> None:
        try:
            self.page.wait_for_timeout(milliseconds)
        except Exception:
            time.sleep(milliseconds / 1_000)

    def _frames(self) -> list[Any]:
        """The main frame first, then nested frames (message bodies are often iframes)."""

        try:
            frames = list(self.page.frames)
        except Exception:
            frames = []
        return frames or [self.page]

    def _find_verification_row(self) -> Any:
        """Return the innermost visible element holding the Replit message row.

        Rows are located by their content (sender and subject in the same short
        element), not by class names. Matching a generic container such as a
        ".mail-list" wrapper would click the middle of an empty inbox area.
        """

        try:
            handle = self.page.evaluate_handle(
                VERIFICATION_ROW_JS,
                [ROW_SENDER_PATTERN, list(ROW_SUBJECT_PATTERNS)],
            )
            return handle.as_element()
        except Exception:
            return None

    def _find_subject_text(self) -> Any:
        """Fallback click target: the visible subject text itself."""

        try:
            locator = self.page.get_by_text(SUBJECT_TEXT_PATTERN)
            for index in range(locator.count()):
                candidate = locator.nth(index)
                if candidate.is_visible(timeout=500):
                    return candidate
        except Exception:
            return None
        return None

    def _refresh_inbox(self) -> None:
        control = self._visible_control(self.REFRESH_SELECTORS)
        if control is None:
            return
        try:
            control.click(timeout=2_000)
        except Exception:
            pass

    def _wait_for_message_listed(self) -> bool:
        """Wait for the Replit row, nudging the inbox's Refresh control now and then."""

        deadline = time.monotonic() + self.timeout_ms / 1_000
        next_refresh = time.monotonic() + self.REFRESH_INTERVAL_SECONDS
        while True:
            if self._find_verification_row() is not None:
                return True
            now = time.monotonic()
            if now >= deadline:
                return False
            if now >= next_refresh:
                self._refresh_inbox()
                next_refresh = now + self.REFRESH_INTERVAL_SECONDS
            self._pause(500)

    def _message_open_now(self) -> bool:
        """True once an opened message (button text or action-code link) is visible."""

        for frame in self._frames():
            try:
                if MESSAGE_OPEN_PATTERN.search(frame.locator("body").inner_text()):
                    return True
            except Exception:
                continue
            try:
                if frame.locator("a[href*='action-code']").count():
                    return True
            except Exception:
                continue
        return False

    def _message_open_observed(self, timeout_ms: int) -> bool:
        deadline = time.monotonic() + timeout_ms / 1_000
        while True:
            if self._message_open_now():
                return True
            if time.monotonic() >= deadline:
                return False
            self._pause(250)

    def _expected_address_missing(self) -> bool:
        """True when the page shows other addresses but not the account's own."""

        if not self.address:
            return False
        shown = extract_email_candidates(self._read_dom_values())
        return bool(shown) and normalize_email(self.address) not in shown

    def open_verification_message(self) -> Any:
        """Wait for and open the Replit message identified by sender and subject."""

        if self.page is None:
            self.open()
        self._to_front()

        if not self._wait_for_message_listed():
            self._save_failure("verification_message_timeout")
            if self._expected_address_missing():
                raise TempMailError(
                    "The mailbox page does not show the address this account was "
                    f"created with ({self.address}). The mailbox session was "
                    "probably lost (new browser profile, or the address was "
                    "changed or deleted), so the verification email cannot arrive "
                    "here. Open the message in the original mailbox by hand and "
                    "use the recovery menu to continue."
                )
            raise TempMailError(
                "The Replit verification message did not appear in the mailbox."
            )

        for finder, wait_ms in (
            (self._find_verification_row, 8_000),
            (self._find_subject_text, 8_000),
        ):
            if self._click_listing(finder) and self._message_open_observed(wait_ms):
                return self.page

        self._save_failure("verification_message_open_failed")
        raise TempMailError("The Replit verification message could not be opened.")

    def _click_listing(self, finder: Any) -> bool:
        """Click the inbox listing ``finder`` locates; True once a click went through.

        A checked click can fail although the listing is plainly on screen: an
        inbox that redraws itself replaces the element between "found" and
        "clicked" (a detached handle is not looked up again), and a slow tab can
        keep Playwright waiting for the element to be stable until the redraw
        comes. The second try therefore looks the listing up again and clicks
        where it is, like a person would, which needs neither. It is skipped when
        something lies on top of the listing: a mouse click would hit that.
        """

        target = finder()
        if target is None:
            return False
        try:
            target.click(timeout=self.OPEN_CLICK_TIMEOUT_MS)
            return True
        except Exception as exc:
            if "intercepts pointer events" in str(exc):
                return False

        target = finder()  # the old handle may be detached
        if target is None:
            return False
        try:
            box = self._box_of(target)
            if not box:
                return False
            self.page.mouse.click(
                box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            )
        except Exception:
            return False
        return True

    def _box_of(self, target: Any) -> Any:
        """Where a located element is on the page (None when it cannot be told)."""

        try:
            return target.bounding_box(timeout=self.LOCATE_BOX_TIMEOUT_MS)
        except TypeError:
            return target.bounding_box()  # an element handle takes no timeout

    def _verification_success_observed(self) -> bool:
        deadline = time.monotonic() + (self.timeout_ms / 1_000)
        saw_verifying_state = False
        while time.monotonic() < deadline:
            self._select_verification_page()
            try:
                if self.page.is_closed():
                    return saw_verifying_state
                text = " ".join(self.page.locator("body").inner_text().split()).casefold()
                if "verifying email" in text:
                    saw_verifying_state = True
                if verification_success_observed(text):
                    return True
            except Exception:
                try:
                    if self.page.is_closed():
                        return saw_verifying_state
                except Exception:
                    pass
            try:
                self.page.wait_for_timeout(250)
            except Exception:
                time.sleep(0.25)
        return False

    def _select_verification_page(self) -> None:
        """Switch to a newly opened Replit verification tab when present."""

        if self.context is None:
            return
        try:
            pages = [page for page in self.context.pages if not page.is_closed()]
        except Exception:
            return
        for page in pages:
            if page is self.page:
                continue
            try:
                page_url = page.url.casefold()
            except Exception:
                continue
            if "replit.com" not in page_url:
                continue
            if "/action-code" in page_url:
                self.page = page
                return
            try:
                body_text = " ".join(page.locator("body").inner_text().split()).casefold()
            except Exception:
                body_text = ""
            if "verifying email" in body_text or verification_success_observed(body_text):
                self.page = page
                return

    def _visible_control_any_frame(self, selectors: tuple[str, ...]) -> Any:
        """First visible control matching ``selectors``, searching nested frames too."""

        for frame in self._frames():
            for selector in selectors:
                try:
                    locator = frame.locator(selector)
                    for index in range(locator.count()):
                        candidate = locator.nth(index)
                        if candidate.is_visible(timeout=500):
                            return candidate
                except Exception:
                    continue
        return None

    def _plain_text_verification_url(self) -> Optional[str]:
        """The validated action-code link printed as text or href in the message."""

        values: list[Any] = []
        for frame in self._frames():
            for read in (
                lambda f: f.locator("body").inner_text(),
                lambda f: f.content(),
            ):
                try:
                    values.append(read(frame))
                except Exception:
                    continue
        return extract_verification_url(values)

    def _open_verification_url(self, url: str) -> bool:
        # Imported here: browser_verification imports this module.
        from browser_verification import verify_action_code_in_browser

        return verify_action_code_in_browser(
            self.context,
            url,
            timeout_seconds=max(30.0, self.timeout_ms / 1_000),
        )

    def _verification_started(self, timeout_ms: int) -> bool:
        """True once a verification page (URL or text) is visible in any tab."""

        deadline = time.monotonic() + timeout_ms / 1_000
        while True:
            self._select_verification_page()
            try:
                if "/action-code" in self.page.url.casefold():
                    return True
                text = " ".join(self.page.locator("body").inner_text().split()).casefold()
                if "verifying email" in text or verification_success_observed(text):
                    return True
            except Exception:
                pass
            if time.monotonic() >= deadline:
                return False
            self._pause(250)

    def verify_email(self) -> bool:
        """Follow the verification link exactly once and prove it worked.

        The link carries a one-time code: visiting it a second time only adds an
        "expired" page next to the real result, so there is never a second click.
        If the message has no usable control (or its destination is not a
        replit.com action-code link) the same message's plain-text link is used;
        only an expected replit.com URL is ever followed.
        """

        self._to_front()
        control = self._visible_control_any_frame(self.VERIFY_NOW_SELECTORS)
        legacy = False
        if control is None:
            control = self._visible_control_any_frame(self.VERIFY_EMAIL_SELECTORS)
            legacy = control is not None

        href = None
        if control is not None:
            try:
                href = control.get_attribute("href")
            except Exception:
                href = None

        if control is None or (href and not is_expected_verification_url(href)):
            url = self._plain_text_verification_url()
            if url is None:
                if control is None:
                    self._save_failure("verification_control_not_found")
                    raise TempMailError(
                        "Neither Verify Now nor Verify Email was visible in the "
                        "message, and no replit.com action-code link was found "
                        "in its text."
                    )
                self._save_failure("verification_control_url_rejected")
                raise TempMailError("The visible verification destination was rejected.")
            self.verification_url = url
            return self._open_verification_url(url)

        if href:
            self.verification_url = href
        try:
            control.click()
        except Exception as exc:
            self._save_failure("verification_control_click_failed")
            raise TempMailError("The verification control could not be clicked.") from exc
        try:
            self.page.wait_for_load_state("domcontentloaded")
        except Exception:
            # The verification page can close itself while navigation is still
            # being observed.
            pass
        self._select_verification_page()

        # Legacy layouts showed "Verify Email" first and "Verify Now" only after
        # that. Press the second control once, and only when the first click
        # visibly started nothing.
        if legacy and not self._verification_started(3_000):
            verify_now = self._visible_control_any_frame(self.VERIFY_NOW_SELECTORS)
            if verify_now is not None:
                now_href = None
                try:
                    now_href = verify_now.get_attribute("href")
                except Exception:
                    pass
                if now_href and not is_expected_verification_url(now_href):
                    self._save_failure("verify_now_url_rejected")
                    raise TempMailError("The Verify Now destination was rejected.")
                if now_href:
                    self.verification_url = now_href
                verify_now.click()
                try:
                    self.page.wait_for_load_state("domcontentloaded")
                except Exception:
                    pass
                self._select_verification_page()

        if self._verification_success_observed():
            return True
        self._save_failure("verification_success_not_observed")
        raise TempMailError(
            "The mailbox flow completed a click, but no verification success "
            "state or successful window close was observed."
        )

    def _mailbox_tab(self) -> Any:
        """The open tab of this mailbox site, or None; it is never navigated.

        ``self.page`` can by now be the verification page, so tabs are matched
        by address, not by identity.
        """

        host = (urlsplit(self.url).hostname or "temp-mail.org").casefold()
        candidates = []
        if self.page is not None:
            candidates.append(self.page)
        try:
            candidates.extend(self.context.pages)
        except Exception:
            pass
        for page in candidates:
            try:
                if not page.is_closed() and host in page.url.casefold():
                    return page
            except Exception:
                continue
        return None

    def _shown_addresses(self) -> list[str]:
        """Every address visible on the page, normalised (senders included)."""

        return [normalize_email(value) for value in extract_email_candidates(self._read_dom_values())]

    def discard_mailbox(self) -> bool:
        """Press the page's Delete button and prove the mailbox is gone.

        Only done when the page still shows THIS run's address, so another
        mailbox is never deleted. Success is this address no longer being on
        the page (a fresh one replaces it). Returns False (never raises) when anything is
        missing or unproven.
        """

        if not self.address:
            return False
        page = self._mailbox_tab()
        if page is None:
            return False
        self.page = page
        self._to_front()
        ours = normalize_email(self.address)
        if ours not in self._shown_addresses():
            return False  # this page shows some other mailbox: leave it alone
        button = self._visible_control(self.DELETE_SELECTORS)
        if button is None:
            self._save_failure("delete_button_not_found")
            return False
        try:
            button.click(timeout=3_000)
        except Exception:
            self._save_failure("delete_click_failed")
            return False
        self._pause(500)
        confirm = self._visible_control(self.DELETE_CONFIRM_SELECTORS)
        if confirm is not None:
            try:
                confirm.click(timeout=3_000)
            except Exception:
                self._save_failure("delete_confirm_failed")
                return False
        deadline = time.monotonic() + self.DELETE_WAIT_MS / 1_000
        while True:
            shown = self._shown_addresses()
            if shown and ours not in shown:
                return True  # the page moved on to a fresh address
            if time.monotonic() >= deadline:
                self._save_failure("delete_not_observed")
                return False
            self._pause(250)

    def _save_failure(self, tag: str) -> None:
        if self.page is None:
            return
        try:
            output_dir = Path("screenshots")
            output_dir.mkdir(exist_ok=True)
            self.page.screenshot(path=str(output_dir / f"temp_mail_{tag}.png"))
        except Exception:
            # Evidence collection must not replace the original failure.
            pass