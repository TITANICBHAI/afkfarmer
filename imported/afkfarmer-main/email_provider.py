"""Shared mailbox-provider contracts.

Keeping the contract in its own module prevents the browser-backed and HTTP
providers from importing each other just to share a base class.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


def normalized_message_text(value: Any) -> str:
    """Normalize mailbox or verification-page text for stable matching."""

    return " ".join(str(value or "").casefold().split())


VERIFICATION_SUCCESS_PHRASES = (
    "email verified",
    "verification successful",
    "email verification success",
    # CORE_FLOW.md 4.5: the page may display "Verifying email success."
    "verifying email success",
    "success! this window will close automatically",
    "your email has been verified",
)


def verification_success_observed(value: Any) -> bool:
    """Return true only for an explicit completed-verification message."""

    text = normalized_message_text(value)
    return any(phrase in text for phrase in VERIFICATION_SUCCESS_PHRASES)


class EmailProviderError(RuntimeError):
    """Raised when a mailbox state or verification result cannot be proven."""


class EmailProvider(ABC):
    """High-level mailbox contract used by the PC orchestrator."""

    address: Optional[str] = None
    verification_url: Optional[str] = None

    @property
    def provider_name(self) -> str:
        """Stable source name persisted in checkpoints for safe resume."""

        return self.__class__.__name__

    def discard_mailbox(self) -> bool:
        """Delete the mailbox once it is no longer needed; True only when proven.

        Providers without a deletable mailbox return False (nothing was deleted).
        """

        return False

    @abstractmethod
    def obtain_address(self) -> str:
        """Return the address to use for account creation."""

    @abstractmethod
    def open_verification_message(self) -> Any:
        """Wait for and load the Replit verification message."""

    @abstractmethod
    def verify_email(self) -> bool:
        """Complete verification and return only after success is observed."""