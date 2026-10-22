import unittest

from pc_flow import (
    classify_session_state,
    classify_signup_form_text,
    classify_signup_state,
    find_validation_errors,
    has_captcha_text,
    has_security_challenge_text,
    strip_recaptcha_notice,
)

# The legal notice every Replit sign-up modal carries
# (attached_assets/step_2.3, 3.2 and 4.png).
NOTICE = (
    "This site is protected by reCAPTCHA Enterprise and the Google "
    "Privacy Policy and Terms of Service apply."
)
CHOOSER_MODAL = (
    "Create a Replit account\nContinue with Google\nContinue with Email\n"
    "View more options\n" + NOTICE
)
FORM_MODAL = (
    "Create an account\nBack\nEmail\nPassword\nEmail is available\n"
    "Password is valid\nCreate Account\n" + NOTICE
)
VERIFY_MODAL = "Verify your email to start building\n" + NOTICE


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


class RecaptchaNoticeTests(unittest.TestCase):
    """The permanent "protected by reCAPTCHA" notice is a disclosure, not a challenge."""

    def test_the_notice_alone_is_not_a_challenge(self):
        for text in (
            NOTICE,
            NOTICE.lower(),
            "This site is protected by reCAPTCHA and the Google Privacy Policy "
            "and Terms of Service apply.",
            "Protected by reCAPTCHA Enterprise",
        ):
            with self.subTest(text=text):
                self.assertFalse(has_captcha_text(text))
                self.assertFalse(has_security_challenge_text(text))

    def test_the_real_modal_texts_are_not_challenges(self):
        for name, text in (
            ("chooser", CHOOSER_MODAL),
            ("form", FORM_MODAL),
            ("verify", VERIFY_MODAL),
        ):
            with self.subTest(modal=name):
                self.assertFalse(has_security_challenge_text(text))

    def test_the_modal_texts_classify_as_waiting_or_submitted(self):
        url = "https://replit.com/"
        self.assertEqual(
            classify_signup_state(url, CHOOSER_MODAL, baseline_url=url), "waiting"
        )
        self.assertEqual(
            classify_signup_state(url, FORM_MODAL, baseline_url=url), "waiting"
        )
        self.assertEqual(
            classify_signup_state(url, VERIFY_MODAL, baseline_url=url), "submitted"
        )

    def test_a_real_challenge_next_to_the_notice_is_still_a_challenge(self):
        for phrase in (
            "Please complete the CAPTCHA",
            "Please complete the reCAPTCHA",
            "I'm not a robot",
            "Please verify you are human",
        ):
            with self.subTest(phrase=phrase):
                text = f"{FORM_MODAL}\n{phrase}"
                self.assertTrue(has_security_challenge_text(text))
                self.assertEqual(
                    classify_signup_state(
                        "https://replit.com/", text, baseline_url="https://replit.com/"
                    ),
                    "captcha",
                )

    def test_other_security_phrases_are_untouched_by_the_notice_rule(self):
        for phrase in (
            "Suspicious activity detected",
            "You have been rate limited",
            "Additional security verification required",
        ):
            with self.subTest(phrase=phrase):
                self.assertTrue(has_security_challenge_text(f"{NOTICE}\n{phrase}"))

    def test_stripping_removes_only_the_notice(self):
        self.assertEqual(
            strip_recaptcha_notice(f"Create an account\n{NOTICE}\nCreate Account"),
            "Create an account Create Account",
        )
        self.assertEqual(strip_recaptcha_notice(None), "")
        self.assertEqual(
            strip_recaptcha_notice("Please complete the CAPTCHA"),
            "Please complete the CAPTCHA",
        )


class SignupFormTextTests(unittest.TestCase):
    """Replit validates both fields asynchronously before the button may be used."""

    def test_both_positive_messages_mean_valid(self):
        self.assertEqual(
            classify_signup_form_text("Email is available\nPassword is valid"),
            "valid",
        )
        self.assertEqual(classify_signup_form_text(FORM_MODAL), "valid")

    def test_one_message_or_none_is_still_pending(self):
        self.assertEqual(classify_signup_form_text("Email is available"), "pending")
        self.assertEqual(classify_signup_form_text("Password is valid"), "pending")
        self.assertEqual(
            classify_signup_form_text("Create an account\nBack\nEmail\nPassword"),
            "pending",
        )
        self.assertEqual(classify_signup_form_text(""), "pending")
        self.assertEqual(classify_signup_form_text(None), "pending")

    def test_an_error_line_means_invalid(self):
        for text in (
            "Email already in use\nPassword is valid",
            "Email is available\nPassword must be at least 8 characters",
            "Invalid email",
        ):
            with self.subTest(text=text):
                self.assertEqual(classify_signup_form_text(text), "invalid")

    def test_new_error_wordings_are_recognised_as_validation_errors(self):
        for line in (
            "This email is already registered",
            "An account with this email already exists",
            "Password is too short",
            "Password is too common",
        ):
            with self.subTest(line=line):
                self.assertEqual(find_validation_errors(line), [line])

    def test_positive_messages_are_not_errors(self):
        self.assertEqual(
            find_validation_errors("Email is available\nPassword is valid"), []
        )

    def test_check_your_email_style_messages_still_count_as_a_result(self):
        for text in (
            "Verify your email to start building",
            "An email was sent to someone@example.test",
            "Check your inbox",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    classify_signup_state(
                        "https://replit.com/", text, baseline_url="https://replit.com/"
                    ),
                    "submitted",
                )


if __name__ == "__main__":
    unittest.main()