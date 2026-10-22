import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from fake_browser import FakeContext, FakeMailTab, VirtualClock
from temp_mail import (
    TempMailError,
    TempMailOrgProvider,
    extract_email_candidates,
    extract_verification_url,
    is_expected_verification_url,
    is_replit_verification_message,
    is_verification_row_text,
    normalize_email,
    provider_block_reason,
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

    def test_verification_message_uses_tolerant_sender_and_subject_matching(self):
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
        self.assertTrue(
            is_replit_verification_message(
                "Replit <verify@replit.com>",
                "Replit: Verify Your Email reminder",
            )
        )
        self.assertFalse(
            is_replit_verification_message(
                "Other Service <verify@replit.com>",
                "Welcome",
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

    def test_provider_block_page_is_classified_without_bypassing_it(self):
        self.assertEqual(
            provider_block_reason(
                "Attention Required! Cloudflare - Sorry, you have been blocked"
            ),
            "provider_blocked",
        )
        self.assertIsNone(provider_block_reason("Mailbox: first@example.com"))

    def test_closed_verification_page_is_not_success_without_observed_state(self):
        provider = TempMailOrgProvider(Mock(), timeout_ms=1)
        provider.page = Mock()
        provider.page.is_closed.return_value = True

        with patch("temp_mail.time.monotonic", return_value=0):
            self.assertFalse(provider._verification_success_observed())

    def test_verification_success_requires_explicit_success_text(self):
        provider = TempMailOrgProvider(Mock(), timeout_ms=1)
        provider.page = Mock()
        provider.page.is_closed.return_value = False
        body = Mock()
        body.inner_text.return_value = "Email verified successfully"
        provider.page.locator.return_value = body

        with patch("temp_mail.time.monotonic", side_effect=[0, 0]):
            self.assertTrue(provider._verification_success_observed())


class VerificationRowTextTests(unittest.TestCase):
    """The Python twin of the in-page row finder (the JS twin runs in a browser)."""

    def test_sender_and_subject_in_one_row_match(self):
        for text in (
            "verify@replit.com Replit: Verify Your Email 1 minute ago",
            "verify@replit.com\nReplit: Verify Your Email",
            "Replit <verify@replit.com> Replit: Verify Your Email",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_verification_row_text(text))

    def test_the_sender_address_alone_is_not_a_subject(self):
        # "verify@replit.com" itself contains "verify" and "replit".
        self.assertFalse(is_verification_row_text("verify@replit.com"))

    def test_other_rows_and_containers_do_not_match(self):
        for text in (
            "Replit: Verify Your Email",  # no sender
            "verify@replit.com Welcome to our newsletter",
            "someone@else.test Verify your Replit email",
            "",
            None,
        ):
            with self.subTest(text=text):
                self.assertFalse(is_verification_row_text(text))


class MailboxFlowTests(unittest.TestCase):
    """open_verification_message / verify_email against scripted mailbox pages.

    The scripted tab follows what the screenshots show: the message may arrive
    late, may need the inbox's Refresh control, and its body may be an iframe.
    Everything runs on a virtual clock.
    """

    ADDRESS = "someone@example.test"
    HREF = FakeMailTab.VALID_HREF

    def setUp(self):
        self.clock = VirtualClock()
        self.context = FakeContext(self.clock)

    def provider(self, **tab_options):
        tab = FakeMailTab(self.context, self.clock, address=self.ADDRESS, **tab_options)
        provider = TempMailOrgProvider(self.context, timeout_ms=30_000)
        provider.page = tab
        provider.address = self.ADDRESS
        provider._save_failure = Mock()
        return provider, tab

    def clocked(self):
        stack = ExitStack()
        stack.enter_context(patch("temp_mail.time", self.clock))
        stack.enter_context(patch("browser_verification.time", self.clock))
        return stack

    def failures(self, provider):
        return [call.args[0] for call in provider._save_failure.call_args_list]

    # -- finding and opening the message ----------------------------------- #

    def test_a_message_that_is_already_there_is_opened_with_one_click(self):
        provider, tab = self.provider(mail_at_s=0.0)

        with self.clocked():
            self.assertIs(provider.open_verification_message(), tab)

        self.assertEqual(tab.row.clicks, 1)
        self.assertEqual(tab.subject.clicks, 0)
        self.assertEqual(tab.refresh.clicks, 0)
        self.assertEqual(self.failures(provider), [])
        self.assertLess(self.clock.elapsed, 3)

    def test_a_slow_message_is_awaited_with_periodic_refreshes(self):
        provider, tab = self.provider(mail_at_s=20.0)

        with self.clocked():
            provider.open_verification_message()

        self.assertEqual(tab.refresh.clicks, 2)  # at 8 s and 16 s, not after arrival
        self.assertEqual(tab.row.clicks, 1)
        self.assertGreaterEqual(self.clock.elapsed, 20)

    def test_an_inbox_that_lists_mail_only_after_a_refresh_gets_one(self):
        provider, tab = self.provider(mail_at_s=3.0, needs_refresh=True)

        with self.clocked():
            provider.open_verification_message()

        self.assertEqual(tab.refresh.clicks, 1)
        self.assertEqual(tab.row.clicks, 1)

    def test_a_message_that_never_arrives_times_out_with_evidence(self):
        provider, tab = self.provider(mail_at_s=None)

        with self.clocked(), self.assertRaises(TempMailError) as caught:
            provider.open_verification_message()

        self.assertIn("did not appear", str(caught.exception))
        self.assertEqual(self.failures(provider), ["verification_message_timeout"])
        self.assertGreaterEqual(self.clock.elapsed, 30)
        self.assertEqual(tab.row.clicks, 0)

    def test_a_mailbox_showing_a_different_address_is_diagnosed(self):
        # The mailbox session was lost (new profile, address changed): the mail
        # can never arrive here, and waiting longer would not help.
        provider, _tab = self.provider(
            mail_at_s=None, shown_address="other@example.test"
        )

        with self.clocked(), self.assertRaises(TempMailError) as caught:
            provider.open_verification_message()

        message = str(caught.exception)
        self.assertIn(self.ADDRESS, message)
        self.assertIn("mailbox session", message)
        self.assertEqual(self.failures(provider), ["verification_message_timeout"])

    def test_a_row_that_opens_nothing_falls_back_to_the_subject_text(self):
        provider, tab = self.provider(row_opens=False)

        with self.clocked():
            provider.open_verification_message()

        self.assertEqual(tab.row.clicks, 1)
        self.assertEqual(tab.subject.clicks, 1)
        self.assertEqual(self.failures(provider), [])

    def test_a_row_click_that_raises_falls_through_to_the_next_target(self):
        provider, tab = self.provider()
        tab.row.click_error = RuntimeError("covered by an overlay")

        with self.clocked():
            provider.open_verification_message()

        self.assertEqual(tab.row.click_attempts, 1)
        self.assertEqual(tab.subject.clicks, 1)

    def test_a_row_covered_by_an_overlay_is_not_clicked_with_the_mouse(self):
        # A mouse click at the row's position would hit whatever lies on top.
        provider, tab = self.provider()
        tab.row.click_error = RuntimeError(
            '<div class="cookie-banner"> intercepts pointer events'
        )

        with self.clocked():
            provider.open_verification_message()

        self.assertEqual(tab.mouse.clicks, [])
        self.assertEqual(tab.subject.clicks, 1)

    def test_nothing_that_opens_the_message_is_a_recorded_failure(self):
        provider, _tab = self.provider(row_opens=False, subject_opens=False)

        with self.clocked(), self.assertRaises(TempMailError):
            provider.open_verification_message()

        self.assertEqual(self.failures(provider), ["verification_message_open_failed"])

    # -- a tab that is not drawn, an inbox that redraws -------------------- #

    def test_the_mailbox_tab_is_brought_to_the_front_before_anything_is_clicked(self):
        provider, tab = self.provider()

        with self.clocked():
            provider.open_verification_message()

        self.assertEqual(tab.actions[0], "front")
        self.assertLess(tab.actions.index("front"), tab.actions.index("row.click"))
        self.assertTrue(tab.in_front)

    def test_a_tab_in_front_is_not_stalled_by_an_inbox_that_redraws(self):
        # The failure seen in the browser simulation: a tab that is not in front
        # shows about one frame a second, so Playwright's wait-until-stable
        # outlasts the inbox's own redraw and the element is gone at click time.
        # Brought to the front first, the first checked click is enough.
        provider, tab = self.provider(redraw_every_s=1.0, starved_frames_s=1.5)

        with self.clocked():
            provider.open_verification_message()

        self.assertEqual(tab.row_handle_clicks, 1)
        self.assertEqual(tab.mouse.clicks, [])
        self.assertEqual(tab.subject.clicks, 0)  # no fallback was needed
        self.assertLess(self.clock.elapsed, 3)

    def test_an_inbox_that_redraws_under_the_click_is_clicked_where_it_is(self):
        # bring_to_front() does nothing here, so the checked click is detached.
        provider, tab = self.provider(
            redraw_every_s=1.0, starved_frames_s=1.5, front_helps=False
        )

        with self.clocked():
            self.assertIs(provider.open_verification_message(), tab)

        self.assertEqual(tab.row_handle_clicks, 1)
        self.assertEqual(len(tab.mouse.clicks), 1)
        x, y = tab.mouse.clicks[0]
        box = tab.ROW_BOX
        self.assertTrue(box["x"] <= x <= box["x"] + box["width"])
        self.assertTrue(box["y"] <= y <= box["y"] + box["height"])
        self.assertEqual(tab.subject.clicks, 0)  # the row did the job
        self.assertEqual(self.failures(provider), [])

    def test_a_mouse_click_that_fails_moves_on_to_the_next_target(self):
        provider, tab = self.provider(
            redraw_every_s=1.0, starved_frames_s=1.5, front_helps=False
        )
        tab.mouse.error = RuntimeError("the page is closed")

        with self.clocked():
            provider.open_verification_message()

        self.assertEqual(tab.subject.clicks, 1)

    def test_a_page_that_cannot_be_brought_forward_is_still_used(self):
        provider, tab = self.provider()
        tab.bring_to_front = Mock(side_effect=RuntimeError("no window to activate"))

        with self.clocked():
            self.assertIs(provider.open_verification_message(), tab)

        tab.bring_to_front.assert_called()

    def test_verifying_brings_the_mailbox_tab_forward_again(self):
        provider, tab = self.provider()

        with self.clocked():
            provider.open_verification_message()
            self.assertEqual(tab.front_calls, 1)
            self.assertTrue(provider.verify_email())

        self.assertEqual(tab.front_calls, 2)

    # -- verifying ---------------------------------------------------------- #

    def test_verify_now_is_followed_exactly_once_in_either_message_layout(self):
        for view in ("iframe", "inline"):
            with self.subTest(view=view):
                self.setUp()
                provider, tab = self.provider(message_view=view)

                with self.clocked():
                    provider.open_verification_message()
                    self.assertTrue(provider.verify_email())

                self.assertEqual(tab.verify_now.clicks, 1)
                self.assertEqual(self.context.opened_urls, [self.HREF])
                self.assertEqual(provider.verification_url, self.HREF)
                self.assertEqual(self.failures(provider), [])

    def test_a_legacy_verify_email_control_that_works_is_the_only_click(self):
        # The page also shows "Verify Now" afterwards; pressing it as well would
        # open the single-use link a second time.
        provider, tab = self.provider(
            verify_control="verify_email", legacy_first_click="starts_and_reveals"
        )

        with self.clocked():
            provider.open_verification_message()
            self.assertTrue(provider.verify_email())

        self.assertEqual(tab.verify_email.clicks, 1)
        self.assertEqual(tab.verify_now.clicks, 0)
        self.assertEqual(len(self.context.opened_urls), 1)

    def test_the_second_legacy_control_is_pressed_only_when_nothing_started(self):
        provider, tab = self.provider(
            verify_control="verify_email", legacy_first_click="reveals_now"
        )

        with self.clocked():
            provider.open_verification_message()
            self.assertTrue(provider.verify_email())

        self.assertEqual(tab.verify_email.clicks, 1)
        self.assertEqual(tab.verify_now.clicks, 1)
        self.assertEqual(len(self.context.opened_urls), 1)

    def test_a_destination_that_is_not_a_replit_action_code_is_never_clicked(self):
        provider, tab = self.provider(
            href="https://evil.example/action-code?x=1", text_url=False
        )

        with self.clocked():
            provider.open_verification_message()
            with self.assertRaises(TempMailError):
                provider.verify_email()

        self.assertEqual(tab.verify_now.click_attempts, 0)
        self.assertEqual(self.context.opened_urls, [])
        self.assertEqual(self.failures(provider), ["verification_control_url_rejected"])

    def test_a_rejected_href_falls_back_to_the_validated_printed_link(self):
        provider, tab = self.provider(
            href="https://evil.example/action-code?x=1", text_href=self.HREF
        )

        with self.clocked(), patch.object(
            provider, "_open_verification_url", return_value=True
        ) as opener:
            provider.open_verification_message()
            self.assertTrue(provider.verify_email())

        opener.assert_called_once_with(self.HREF)
        self.assertEqual(tab.verify_now.click_attempts, 0)
        self.assertEqual(provider.verification_url, self.HREF)

    def test_a_message_without_a_control_uses_the_printed_link_through_the_verifier(self):
        provider, tab = self.provider(verify_control="none")

        with self.clocked():
            provider.open_verification_message()
            self.assertTrue(provider.verify_email())

        verification_tab = self.context.pages[-1]
        self.assertEqual(verification_tab.goto_calls, [self.HREF])
        self.assertTrue(verification_tab.closed)
        self.assertEqual(provider.verification_url, self.HREF)
        self.assertEqual(tab.verify_now.click_attempts, 0)

    def test_neither_a_control_nor_a_link_is_a_recorded_failure(self):
        provider, tab = self.provider(verify_control="none", text_url=False)
        tab.opened_at = self.clock.now - 10  # the message is on screen

        with self.clocked(), self.assertRaises(TempMailError):
            provider.verify_email()

        self.assertEqual(self.failures(provider), ["verification_control_not_found"])

    def test_a_control_that_cannot_be_clicked_is_a_recorded_failure(self):
        provider, _tab = self.provider(click_error=RuntimeError("detached"))

        with self.clocked():
            provider.open_verification_message()
            with self.assertRaises(TempMailError):
                provider.verify_email()

        self.assertEqual(self.failures(provider), ["verification_control_click_failed"])
        self.assertEqual(self.context.opened_urls, [])

    def test_a_verification_page_that_never_succeeds_fails_without_a_second_visit(self):
        self.context.verification_script = [(0.0, "Verifying email")]
        provider, tab = self.provider()

        with self.clocked():
            provider.open_verification_message()
            with self.assertRaises(TempMailError):
                provider.verify_email()

        self.assertEqual(tab.verify_now.clicks, 1)
        self.assertEqual(len(self.context.opened_urls), 1)  # single-use link
        self.assertEqual(self.failures(provider), ["verification_success_not_observed"])
        self.assertGreaterEqual(self.clock.elapsed, 30)

    def test_a_window_that_closes_itself_after_verifying_counts_as_success(self):
        self.context.verification_script = [(0.0, "Verifying email")]
        self.context.tab_closes_after_s = 2.0
        provider, _tab = self.provider()

        with self.clocked():
            provider.open_verification_message()
            self.assertTrue(provider.verify_email())

    def test_a_window_that_closes_before_showing_anything_is_not_success(self):
        self.context.verification_script = [(0.0, "")]
        self.context.tab_closes_after_s = 1.0
        provider, _tab = self.provider()

        with self.clocked():
            provider.open_verification_message()
            with self.assertRaises(TempMailError):
                provider.verify_email()


if __name__ == "__main__":
    unittest.main()