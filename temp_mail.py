"""Browser-backed temporary-mail provider primitives.

This module does not open a browser on import. A provider only contacts the
configured site when ``obtain_address`` is called by the orchestrator.
"""

from __future__ import annotations

import re
from html import unescape
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from email_providers import EmailProvider


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


class TempMailError(RuntimeError):
    """Raised when a mailbox state cannot be proven."""


EmailProviderError = TempMailError

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
    MESSAGE_ROW_SELECTORS = (
        "tr",
        "[role='listitem']",
        "[data-testid*='message' i]",
        "[data-testid*='mail' i]",
        "[class*='message' i]",
        "[class*='mail' i]",
    )
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

        self.page.goto(self.url, wait_until="domcontentloaded")
        self.page.wait_for_load_state("domcontentloaded")
        return self.page

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

    def _message_rows(self) -> list[Any]:
        rows: list[Any] = []
        seen: set[str] = set()
        for selector in self.MESSAGE_ROW_SELECTORS:
            locator = self.page.locator(selector)
            try:
                for index in range(locator.count()):
                    row = locator.nth(index)
                    text = " ".join(row.inner_text().split())
                    if text and text not in seen:
                        rows.append(row)
                        seen.add(text)
            except Exception:
                continue
        return rows

    def open_verification_message(self) -> Any:
        """Wait for and open the Replit message identified by sender and subject."""

        if self.page is None:
            self.open()
        try:
            self.page.wait_for_function(
                """() => {
                    const text = document.body?.innerText || "";
                            return /verify@replit\.com/i.test(text) &&
                                   /replit.*(verify|verification)|(verify|verification).*replit/i.test(text);
                }""",
                timeout=self.timeout_ms,
            )
        except Exception as exc:
            self._save_failure("verification_message_timeout")
            raise TempMailError(
                "The Replit verification message did not appear in the mailbox."
            ) from exc

        for row in self._message_rows():
            try:
                text = row.inner_text()
            except Exception:
                continue
            normalized_text = " ".join(text.split()).casefold()
            if (
                "verify@replit.com" in normalized_text
                and is_replit_verification_message(
                    "verify@replit.com",
                    normalized_text,
                )
            ):
                try:
                    row.click()
                    self.page.wait_for_function(
                        """() => {
                            const text = document.body?.innerText || "";
                            return /verify\s+(email|now)|verification|action-code/i.test(text);
                        }""",
                        timeout=self.timeout_ms,
                    )
                    return self.page
                except Exception as exc:
                    self._save_failure("verification_message_open_failed")
                    raise TempMailError(
                        "The Replit verification message could not be opened."
                    ) from exc

        self._save_failure("verification_message_not_matched")
        raise TempMailError(
            "A Replit verification message was visible, but its sender and "
            "subject could not be matched."
        )

    def _follow_control(self, selectors: tuple[str, ...], failure_tag: str) -> Any:
        control = self._visible_control(selectors)
        if control is None:
            self._save_failure(failure_tag)
            raise TempMailError(f"Mailbox control not found: {failure_tag}.")
        href = None
        try:
            href = control.get_attribute("href")
        except Exception:
            pass
        if href and not is_expected_verification_url(href):
            self._save_failure(f"{failure_tag}_url_rejected")
            raise TempMailError("The visible verification destination was rejected.")
        try:
            control.click()
            return self.page
        except Exception as exc:
            self._save_failure(f"{failure_tag}_click_failed")
            raise TempMailError(f"Mailbox control could not be clicked: {failure_tag}.") from exc

    def _verification_success_observed(self) -> bool:
        deadline = time.monotonic() + (self.timeout_ms / 1_000)
        while time.monotonic() < deadline:
            try:
                if self.page.is_closed():
                    return True
                text = " ".join(self.page.locator("body").inner_text().split()).casefold()
                if any(
                    phrase in text
                    for phrase in (
                        "email verified",
                        "verification successful",
                        "verifying email",
                        "email verification success",
                    )
                ):
                    return True
            except Exception:
                try:
                    if self.page.is_closed():
                        return True
                except Exception:
                    pass
            try:
                self.page.wait_for_timeout(250)
            except Exception:
                time.sleep(0.25)
        return False

    def verify_email(self) -> bool:
        """Follow direct Verify Now or legacy Verify Email controls."""

        control = self._visible_control(self.VERIFY_NOW_SELECTORS)
        if control is None:
            control = self._visible_control(self.VERIFY_EMAIL_SELECTORS)
        if control is None:
            self._save_failure("verification_control_not_found")
            raise TempMailError(
                "Neither Verify Now nor Verify Email was visible in the message."
            )

        href = None
        try:
            href = control.get_attribute("href")
        except Exception:
            pass
        if href and not is_expected_verification_url(href):
            self._save_failure("verification_control_url_rejected")
            raise TempMailError("The visible verification destination was rejected.")
        try:
            control.click()
            self.page.wait_for_load_state("domcontentloaded")
        except Exception as exc:
            self._save_failure("verification_control_click_failed")
            raise TempMailError("The verification control could not be clicked.") from exc

        # Some message layouts expose Verify Email first and Verify Now only
        # after the message content has rendered.
        verify_now = self._visible_control(self.VERIFY_NOW_SELECTORS)
        if verify_now is not None and verify_now is not control:
            href = verify_now.get_attribute("href")
            if href and not is_expected_verification_url(href):
                self._save_failure("verify_now_url_rejected")
                raise TempMailError("The Verify Now destination was rejected.")
            verify_now.click()
            try:
                self.page.wait_for_load_state("domcontentloaded")
            except Exception:
                pass

        if self._verification_success_observed():
            return True
        self._save_failure("verification_success_not_observed")
        raise TempMailError(
            "The mailbox flow completed a click, but no verification success "
            "state or successful window close was observed."
        )

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