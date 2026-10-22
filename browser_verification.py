"""Verify a Replit action-code link in the browser and observe the rendered result.

An HTTP GET cannot prove verification: the success state is rendered in the
page, so the raw response body may never contain it. The link is therefore
opened in the operator's browser context and the visible page text is observed,
the same contract the temp-mail.org provider uses.
"""

from __future__ import annotations

import time
from typing import Any

from email_provider import EmailProviderError, verification_success_observed
from temp_mail import is_expected_verification_url


def verify_action_code_in_browser(
    context: Any,
    url: str,
    timeout_seconds: float = 60.0,
    poll_seconds: float = 0.5,
) -> bool:
    """Open ``url`` in a new tab and return True only after success is observed.

    CORE_FLOW.md 4.5 allows the verification window to close by itself, so a tab
    that disappears after it displayed "Verifying email" counts as success. A tab
    that closes before showing that state does not.
    """

    if not is_expected_verification_url(url):
        raise EmailProviderError("The verification destination was rejected before navigation.")

    page = context.new_page()
    saw_verifying_state = False
    try:
        page.goto(url, wait_until="domcontentloaded")
        deadline = time.monotonic() + max(1.0, float(timeout_seconds))
        while time.monotonic() < deadline:
            try:
                text = " ".join(page.locator("body").inner_text().split()).casefold()
            except Exception as exc:
                if saw_verifying_state:
                    return True
                raise EmailProviderError(
                    "The verification page closed before a verifying or success "
                    "state was observed."
                ) from exc
            if "verifying email" in text:
                saw_verifying_state = True
            if verification_success_observed(text):
                return True
            time.sleep(max(0.01, float(poll_seconds)))
        raise EmailProviderError(
            "The verification page did not show an observable success state "
            "before the timeout."
        )
    finally:
        try:
            page.close()
        except Exception:
            # The page may already have closed itself after verification.
            pass
