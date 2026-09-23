import unittest

from pc_flow import (
    classify_session_state,
    classify_signup_state,
    find_validation_errors,
    has_captcha_text,
    has_security_challenge_text,
)


class PcFlowClassificationTests(unittest.TestCase):
    def test_captcha_text_requires_manual_handling(self):
        self.assertTrue(has_captcha_text("Please verify you are human"))
        self.assertTrue(has_captcha_text("reCAPTCHA challenge"))
        self.assertFalse(has_captcha_text("Check your inbox"))
        self.assertTrue(has_security_challenge_text("Suspicious login detected"))
        self.assertTrue(
            has_security_challenge_text("Additional identity verification required")
        )
        self.assertFalse(has_security_challenge_text("Check your inbox"))

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
                "https://replit.com/",
                "Create your account",
                baseline_url="https://replit.com/",
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

    def test_session_state_requires_authenticated_content(self):
        self.assertEqual(
            classify_session_state(
                "https://replit.com/",
                "Welcome back. Sign in to continue.",
            ),
            "unknown",
        )
        self.assertEqual(
            classify_session_state(
                "https://replit.com/login",
                "Log in to Replit",
            ),
            "login_required",
        )
        self.assertEqual(
            classify_session_state(
                "https://replit.com/",
                "Personal workspace My Repls",
            ),
            "authenticated",
        )


if __name__ == "__main__":
    unittest.main()