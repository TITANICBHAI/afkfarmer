"""Shared mailbox-provider contracts.

Keeping the contract in its own module prevents the browser-backed and HTTP
providers from importing each other just to share a base class.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


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

    @abstractmethod
    def obtain_address(self) -> str:
        """Return the address to use for account creation."""

    @abstractmethod
    def open_verification_message(self) -> Any:
        """Wait for and load the Replit verification message."""

    @abstractmethod
    def verify_email(self) -> bool:
        """Complete verification and return only after success is observed."""