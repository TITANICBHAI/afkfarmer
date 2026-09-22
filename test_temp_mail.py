import unittest

from temp_mail import (
    extract_email_candidates,
    extract_verification_url,
    is_expected_verification_url,
    is_replit_verification_message,
    normalize_email,
)


class TempMailParsingTests(unittest.TestCase):
    def test_normalize_email_removes_whitespace_and_zero_width_chars(self):
        self.assertEqual(
            normalize_email("  User\u200b@Example.COM \n"),
            "user@example.com",
        )

    def test_extract_email_candidates_preserves_order_and_deduplicates(self):
        values = [
            "Loading...",
            "Mailbox: first@example.com",
            "first@example.com",
            "Reply to second@example.org",
        ]
        self.assertEqual(
            extract_email_candidates(values),
            ["first@example.com", "second@example.org"],
        )

    def test_verification_message_requires_sender_and_exact_subject(self):
        self.assertTrue(
            is_replit_verification_message(
                "Replit <verify@replit.com>",
                "Replit verify email",
            )
        )
        self.assertFalse(
            is_replit_verification_message(
                "Other Service <verify@replit.com>",
                "Welcome",
            )
        )
        self.assertFalse(
            is_replit_verification_message(
                "Replit <verify@replit.com>",
                "Replit verify email reminder",
            )
        )

    def test_verification_url_extraction_requires_expected_replit_host(self):
        valid = "https://replit.com/action-code/abc123?token=redacted"
        self.assertEqual(
            extract_verification_url(
                [f'<a href="{valid.replace("&", "&amp;")}">Verify Email</a>']
            ),
            valid,
        )
        self.assertTrue(is_expected_verification_url(valid))
        self.assertFalse(
            is_expected_verification_url(
                "https://replit.com.evil.example/action-code/abc"
            )
        )
        self.assertFalse(
            is_expected_verification_url("http://replit.com/action-code/abc")
        )
        self.assertFalse(
            is_expected_verification_url("https://replit.com/action-code-evil/abc")
        )


if __name__ == "__main__":
    unittest.main()