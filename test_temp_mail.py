import unittest

from temp_mail import extract_email_candidates, normalize_email


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


if __name__ == "__main__":
    unittest.main()