"""Browser-backed temporary-mail provider primitives.

This module does not open a browser on import. A provider only contacts the
configured site when ``obtain_address`` is called by the orchestrator.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional


EMAIL_PATTERN = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    flags=re.IGNORECASE,
)


class TempMailError(RuntimeError):
    """Raised when a mailbox state cannot be proven."""


def normalize_email(value: Any) -> str:
    """Return a normalized candidate email address or an empty string."""

    if value is None:
        return ""
    return str(value).replace("\u200b", "").strip().lower()


def extract_email_candidates(values: list[Any]) -> list[str]:
    """Extract unique email candidates while preserving discovery order."""

    candidates: list[str] = []
    for value in values:
        for match in EMAIL_PATTERN.findall(normalize_email(value)):
            candidate = normalize_email(match)
            if candidate and candidate not in candidates:
                candidates.append(candidate)
    return candidates


class TempMailProvider(ABC):
    """Small interface used by the PC orchestrator."""

    @abstractmethod
    def obtain_address(self) -> str:
        """Open the provider and return a verified mailbox address."""


class TempMailOrgProvider(TempMailProvider):
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