import unittest

from pc_flow import classify_signup_state, find_validation_errors, has_captcha_text


class PcFlowClassificationTests(unittest.TestCase):
    def test_captcha_text_requires_manual_handling(self):
        self.assertTrue(has_captcha_text("Please verify you are human"))
        self.assertTrue(has_captcha_text("reCAPTCHA challenge"))
        self.assertFalse(has_captcha_text("Check your inbox"))

    def test_validation_errors_are_extracted_and_deduplicated(self):
        body = """
        Email is required
        Email is required
        Password must be 8 characters
        """
        self.assertEqual(
            find_validation_errors(body),
            ["Email is required", "Password must be 8 characters"],
        )

    def test_signup_state_requires_observed_result(self):
        self.assertEqual(
            classify_signup_state(
                "https://replit.com/signup",
                "Create your account",
            ),
            "waiting",
        )
        self.assertEqual(
            classify_signup_state(
                "https://replit.com/signup",
                "Check your inbox for the verification email",
            ),
            "submitted",
        )
        self.assertEqual(
            classify_signup_state(
                "https://replit.com/signup",
                "Email is required",
            ),
            "validation",
        )
        self.assertEqual(
            classify_signup_state(
                "https://replit.com/signup",
                "Please verify you are human",
            ),
            "captcha",
        )


if __name__ == "__main__":
    unittest.main()