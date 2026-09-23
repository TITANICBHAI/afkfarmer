"""Provider-neutral mailbox contracts and the 1secmail implementation.

The automation only depends on the small EmailProvider interface. Browser
mailboxes and HTTP mailboxes can therefore be selected without putting their
protocol details into the Replit flow.
"""

from __future__ import annotations

import re
import time
import uuid
from html import unescape
from typing import Any, Optional

import requests

from email_provider import (
    EmailProvider,
    EmailProviderError,
    verification_success_observed,
)
from temp_mail import (
    extract_verification_url,
    is_replit_verification_message,
    TempMailOrgProvider,
)


class ManualEmailProvider(EmailProvider):
    """Provider for a user-owned address whose mailbox is handled manually."""

    provider_name = "custom"

    def __init__(self, address: str):
        self.address = address.strip()

    def obtain_address(self) -> str:
        if not self.address:
            raise EmailProviderError("A custom email address was empty.")
        return self.address

    def open_verification_message(self) -> Any:
        raise EmailProviderError(
            "Custom-email mode does not access the mailbox automatically. "
            "Open the message manually and use the recovery menu to continue."
        )

    def verify_email(self) -> bool:
        raise EmailProviderError(
            "Custom-email verification requires explicit manual takeover."
        )


class FallbackEmailProvider(EmailProvider):
    """Use a primary provider and create the fallback only when needed.

    Fallback after a mailbox has been created would produce a different email
    address and could never verify the account that was just registered. The
    fallback is therefore limited to address acquisition.
    """

    def __init__(self, primary: EmailProvider, fallback_factory: Any):
        self.primary = primary
        self.fallback_factory = fallback_factory
        self.active: Optional[EmailProvider] = None
        self.address: Optional[str] = None
        self.verification_url: Optional[str] = None

    @property
    def provider_name(self) -> str:
        return self.active.provider_name if self.active is not None else "hybrid"

    def obtain_address(self) -> str:
        try:
            address = self.primary.obtain_address()
            self.active = self.primary
        except EmailProviderError:
            fallback = self.fallback_factory()
            address = fallback.obtain_address()
            self.active = fallback
        self.address = address
        return address

    def _active_provider(self) -> EmailProvider:
        if self.active is None:
            self.obtain_address()
        if self.active is None:
            raise EmailProviderError("No email provider was selected.")
        return self.active

    def open_verification_message(self) -> Any:
        message = self._active_provider().open_verification_message()
        self.verification_url = self._active_provider().verification_url
        return message

    def verify_email(self) -> bool:
        result = self._active_provider().verify_email()
        self.verification_url = self._active_provider().verification_url
        return result


def create_email_provider(
    *,
    context: Any,
    strategy: str,
    primary_api: str,
    custom_email: str,
    timeout_seconds: float,
    temp_mail_url: str,
) -> EmailProvider:
    """Build the configured provider chain without opening any mailbox yet."""

    if custom_email.strip():
        return ManualEmailProvider(custom_email)

    selected = strategy.strip().casefold()
    if selected not in {"hybrid", "api", "temp-mail.org"}:
        raise ValueError(
            "EMAIL_STRATEGY must be one of: hybrid, api, temp-mail.org."
        )

    def browser_provider() -> TempMailOrgProvider:
        if context is None:
            raise EmailProviderError("A browser context is required for temp-mail.org.")
        return TempMailOrgProvider(context, url=temp_mail_url)

    if selected == "temp-mail.org":
        return browser_provider()

    if primary_api.casefold() != "1secmail":
        raise ValueError(
            f"Unsupported PRIMARY_EMAIL_API: {primary_api}. "
            "Only 1secmail is currently implemented."
        )

    primary = OneSecMailProvider(timeout_seconds=timeout_seconds)
    if selected == "api":
        return primary
    return FallbackEmailProvider(primary, browser_provider)


class OneSecMailProvider(EmailProvider):
    """One-time 1secmail mailbox with bounded polling and URL validation."""

    API_URL = "https://www.1secmail.com/api/v1/"
    provider_name = "1secmail"
    _USERNAME_PATTERN = re.compile(r"^[a-z0-9._-]+$", re.IGNORECASE)
    _DOMAIN_PATTERN = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$", re.IGNORECASE)

    def __init__(
        self,
        timeout_seconds: float = 60,
        poll_interval_seconds: float = 2,
        request_timeout_seconds: float = 10,
        session: Any = requests,
    ):
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.poll_interval_seconds = max(0.1, float(poll_interval_seconds))
        self.request_timeout_seconds = max(1.0, float(request_timeout_seconds))
        self.session = session
        self.address: Optional[str] = None
        self.verification_url: Optional[str] = None
        self.message: Optional[dict[str, Any]] = None

    @staticmethod
    def _json_response(response: Any) -> Any:
        if getattr(response, "status_code", 200) < 200 or getattr(
            response, "status_code", 200
        ) >= 300:
            raise EmailProviderError(
                f"1secmail returned HTTP {getattr(response, 'status_code', 'unknown')}."
            )
        try:
            return response.json()
        except (ValueError, TypeError) as exc:
            raise EmailProviderError("1secmail returned malformed JSON.") from exc

    def _get(self, params: dict[str, str]) -> Any:
        try:
            response = self.session.get(
                self.API_URL,
                params=params,
                timeout=self.request_timeout_seconds,
            )
        except Exception as exc:
            raise EmailProviderError("1secmail request failed.") from exc
        return self._json_response(response)

    @staticmethod
    def _parse_address(value: Any) -> tuple[str, str, str]:
        address = str(value or "").strip().lower()
        if "@" not in address:
            raise EmailProviderError("1secmail returned an invalid mailbox address.")
        login, domain = address.split("@", 1)
        if not OneSecMailProvider._USERNAME_PATTERN.fullmatch(login):
            raise EmailProviderError("1secmail returned an invalid mailbox login.")
        if not OneSecMailProvider._DOMAIN_PATTERN.fullmatch(domain):
            raise EmailProviderError("1secmail returned an invalid mailbox domain.")
        return login, domain, address

    def obtain_address(self) -> str:
        data = self._get({"action": "genRandomMailbox", "count": "1"})
        if not isinstance(data, list) or len(data) != 1:
            raise EmailProviderError("1secmail did not return one mailbox address.")
        _login, _domain, address = self._parse_address(data[0])
        self.address = address
        return address

    def _mailbox_parts(self) -> tuple[str, str]:
        if not self.address:
            self.obtain_address()
        login, domain, _address = self._parse_address(self.address)
        return login, domain

    @staticmethod
    def _message_matches(message: Any) -> bool:
        if not isinstance(message, dict):
            return False
        return is_replit_verification_message(
            message.get("from") or message.get("sender"),
            message.get("subject"),
        )

    def _list_messages(self) -> list[dict[str, Any]]:
        login, domain = self._mailbox_parts()
        data = self._get(
            {
                "action": "getMessages",
                "login": login,
                "domain": domain,
            }
        )
        if not isinstance(data, list):
            raise EmailProviderError("1secmail returned an invalid message list.")
        return [message for message in data if isinstance(message, dict)]

    def _read_message(self, message_id: Any) -> dict[str, Any]:
        login, domain = self._mailbox_parts()
        if message_id is None or str(message_id).strip() == "":
            raise EmailProviderError("1secmail returned a message without an ID.")
        data = self._get(
            {
                "action": "readMessage",
                "login": login,
                "domain": domain,
                "id": str(message_id),
            }
        )
        if not isinstance(data, dict):
            raise EmailProviderError("1secmail returned an invalid message body.")
        return data

    def open_verification_message(self) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout_seconds
        last_error: Optional[Exception] = None
        while time.monotonic() < deadline:
            try:
                messages = self._list_messages()
                candidate = next(
                    (message for message in messages if self._message_matches(message)),
                    None,
                )
                if candidate is not None:
                    message = self._read_message(candidate.get("id"))
                    values = [
                        message.get("body"),
                        message.get("textBody"),
                        message.get("htmlBody"),
                        candidate.get("subject"),
                        candidate.get("from"),
                    ]
                    verification_url = extract_verification_url(
                        [unescape(str(value or "")) for value in values]
                    )
                    if not verification_url:
                        raise EmailProviderError(
                            "The Replit message did not contain a valid verification URL."
                        )
                    self.message = message
                    self.verification_url = verification_url
                    return message
            except EmailProviderError as exc:
                last_error = exc
                if "did not contain" in str(exc):
                    raise
            if time.monotonic() >= deadline:
                break
            time.sleep(min(self.poll_interval_seconds, max(0.01, deadline - time.monotonic())))

        if last_error:
            raise EmailProviderError(
                "The 1secmail verification message was not available before timeout."
            ) from last_error
        raise EmailProviderError(
            "The 1secmail verification message was not available before timeout."
        )

    def verify_email(self) -> bool:
        if not self.verification_url:
            self.open_verification_message()
        if not self.verification_url:
            raise EmailProviderError("No validated verification URL was available.")
        try:
            response = self.session.get(
                self.verification_url,
                timeout=self.request_timeout_seconds,
                allow_redirects=True,
            )
        except Exception as exc:
            raise EmailProviderError("Following the verification URL failed.") from exc
        if response.status_code < 200 or response.status_code >= 400:
            raise EmailProviderError(
                f"Verification did not return an acceptable HTTP status "
                f"({response.status_code})."
            )
        response_text = " ".join(
            str(getattr(response, "text", "") or "").casefold().split()
        )
        if not verification_success_observed(response_text):
            raise EmailProviderError(
                "Verification request completed without an observable success state."
            )
        return True