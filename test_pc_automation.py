import contextlib
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fake_browser import FakeReplitSignup, VirtualClock
from pc_automation import PCAutomation


class FakeLocator:
    def __init__(self, page, visible=True, text=""):
        self.page = page
        self.visible = visible
        self.text = text
        self.value = None
        self.pressed = None

    @property
    def first(self):
        return self

    def count(self):
        return 1 if self.visible else 0

    def nth(self, _index):
        return self

    def is_visible(self, timeout=0):
        if hasattr(self.page, "visibility_timeouts"):
            self.page.visibility_timeouts.append(timeout)
        return self.visible

    def wait_for(self, state="visible", timeout=0):
        if state == "visible" and not self.visible:
            raise TimeoutError("locator is hidden")

    def click(self, **_kwargs):
        return None

    def fill(self, value):
        self.value = value

    def press(self, key):
        self.pressed = key
        if key == "Enter":
            self.page.url = "https://replit.com/~/imported"

    def inner_text(self):
        return self.text


class FakePage:
    def __init__(self, *, chat_visible=True, wait_succeeds=True):
        self.url = "https://replit.com/"
        self.chat = FakeLocator(self, visible=chat_visible)
        self.wait_succeeds = wait_succeeds
        self.screenshot_paths = []

    def locator(self, selector):
        if selector.startswith("textarea") or selector.startswith("input[placeholder"):
            return self.chat
        return FakeLocator(self, visible=False)

    def wait_for_function(self, _script, timeout=0, **_kwargs):
        if not self.wait_succeeds:
            raise TimeoutError("import state not observed")

    def screenshot(self, path):
        self.screenshot_paths.append(path)


class SessionSyncPage:
    def __init__(self, states):
        self.states = list(states)
        self.url = "https://replit.com/"
        self.body_text = ""
        self.reload_count = 0
        self.waits = []
        self.screenshot_paths = []

    def reload(self, wait_until=None, timeout=0):
        self.reload_count += 1
        self.url, self.body_text = self.states.pop(0)

    def wait_for_load_state(self, state, timeout=0):
        self.waits.append((state, timeout))

    def wait_for_timeout(self, timeout):
        self.waits.append(("poll", timeout))

    def locator(self, selector):
        if selector == "body":
            body = FakeLocator(self)
            body.inner_text = lambda: self.body_text
            return body
        return FakeLocator(self, visible=False)

    def screenshot(self, path):
        self.screenshot_paths.append(path)


class PcAutomationMockTests(unittest.TestCase):
    def make_automation(self, page):
        automation = PCAutomation("mail@example.test", "password")
        automation.page = page
        return automation

    def test_explicit_cdp_endpoint_skips_local_port_probe(self):
        automation = PCAutomation("mail@example.test", "password")

        with patch.dict(
            "os.environ",
            {"PLAYWRIGHT_CDP_URL": "http://127.0.0.1:9876"},
            clear=False,
        ):
            with patch("pc_automation.urlopen") as urlopen:
                self.assertEqual(
                    automation._candidate_cdp_urls(),
                    ["http://127.0.0.1:9876"],
                )

        urlopen.assert_not_called()

    def test_managed_browser_uses_regular_context_and_storage_state(self):
        automation = PCAutomation("mail@example.test", "password")
        playwright = Mock()
        browser = Mock()
        context = Mock()
        page = Mock()
        context.pages = []
        context.new_page.return_value = page
        browser.new_context.return_value = context
        playwright.chromium.launch.return_value = browser
        automation.playwright = playwright

        automation._launch_managed_context(storage_state="auth_state.json")

        playwright.chromium.launch.assert_called_once()
        browser.new_context.assert_called_once_with(
            storage_state="auth_state.json",
        )
        self.assertIs(automation.browser, browser)
        self.assertIs(automation.context, context)
        self.assertIs(automation.page, page)
        self.assertTrue(automation._owns_browser)

    def test_import_requires_visible_control_and_observed_project_url(self):
        page = FakePage(chat_visible=True)
        automation = self.make_automation(page)

        with patch("pc_automation.PlaywrightTimeoutError", TimeoutError):
            self.assertTrue(
                automation.import_github_repo(
                    "https://github.com/example/project"
                )
            )

        self.assertEqual(page.chat.value, "Import this GitHub repository: https://github.com/example/project")
        self.assertEqual(page.chat.pressed, "Enter")
        self.assertIn("/~/", page.url)

    def test_import_control_missing_is_failure_and_saves_evidence(self):
        page = FakePage(chat_visible=False)
        automation = self.make_automation(page)

        with patch.object(automation, "_save_failure") as save_failure:
            self.assertFalse(
                automation.import_github_repo(
                    "https://github.com/example/project"
                )
            )

        save_failure.assert_called_once_with("github_import_control_not_found")

    def test_import_timeout_is_failure_and_saves_evidence(self):
        page = FakePage(chat_visible=True, wait_succeeds=False)
        automation = self.make_automation(page)

        with patch("pc_automation.PlaywrightTimeoutError", TimeoutError):
            with patch.object(automation, "_save_failure") as save_failure:
                self.assertFalse(
                    automation.import_github_repo(
                        "https://github.com/example/project"
                    )
                )

        save_failure.assert_called_once_with("github_import_result_timeout")

    def test_session_sync_reloads_until_authenticated_state_is_observed(self):
        page = SessionSyncPage(
            [
                ("https://replit.com/", "Loading workspace"),
                ("https://replit.com/~/home", "Personal workspace"),
            ]
        )
        automation = self.make_automation(page)

        self.assertEqual(
            automation.wait_for_session_sync(timeout=5_000, poll_interval=1),
            "authenticated",
        )
        self.assertEqual(page.reload_count, 2)
        self.assertIn("poll", [kind for kind, _timeout in page.waits])

    def test_session_sync_timeout_remains_unknown_and_saves_evidence(self):
        page = SessionSyncPage(
            [("https://replit.com/", "Still loading")]
        )
        automation = self.make_automation(page)

        with patch.object(automation, "_save_failure") as save_failure:
            self.assertEqual(
                automation.wait_for_session_sync(timeout=0),
                "unknown",
            )

        save_failure.assert_called_once_with("session_sync_timeout")

    def test_close_browser_does_not_raise_when_managed_browser_is_already_gone(self):
        automation = PCAutomation("mail@example.test", "password")
        browser = Mock()
        browser.is_connected.return_value = True
        browser.close.side_effect = RuntimeError("Target page already closed")
        playwright = Mock()
        automation.browser = browser
        automation.playwright = playwright
        automation._owns_browser = True

        with patch.object(automation, "save_state"):
            automation.close_browser()

        browser.close.assert_called_once()
        playwright.stop.assert_called_once()
        self.assertIsNone(automation.browser)
        self.assertIsNone(automation.playwright)

    def test_close_browser_never_closes_an_attached_browser(self):
        automation = PCAutomation("mail@example.test", "password")
        browser = Mock()
        playwright = Mock()
        automation.browser = browser
        automation.playwright = playwright
        automation._owns_browser = False

        with patch.object(automation, "save_state"):
            automation.close_browser()

        browser.close.assert_not_called()
        playwright.stop.assert_called_once()


class OperatorDoubles:
    """The person at the keyboard, on the virtual clock.

    Records what the run did to get their attention: every sound (``alerts``,
    as virtual times), every ENTER prompt it opened (``prompts``) and the order
    of the keyboard calls (``calls``). It presses ENTER ``enter_after_s`` seconds
    after a challenge appeared (never when None), once per appearance.

    It solves nothing by itself: finishing a challenge is the page double's
    ``human_s`` (a person acting in the browser) or, when the run has to ask for
    ENTER, the person doing it then.
    """

    def __init__(self, clock, page, enter_after_s=None, beep_error=None):
        self.clock = clock
        self.page = page
        self.enter_after_s = enter_after_s
        self.beep_error = beep_error
        self.alerts = []
        self.prompts = []
        self.calls = []
        self._pressed = set()
        self._stack = contextlib.ExitStack()

    def beep(self):
        self.alerts.append(self.clock.now)
        if self.beep_error is not None:
            raise self.beep_error

    def discard_pending_input(self):
        self.calls.append("discard")

    def enter_pressed(self):
        self.calls.append("enter?")
        shown = self.page.shown_at
        if self.enter_after_s is None or shown is None:
            return False
        if self.clock.now - shown < self.enter_after_s or shown in self._pressed:
            return False
        self._pressed.add(shown)
        return True

    def ask(self, prompt=""):
        """A blocking input() prompt: the person solves it, then presses ENTER."""

        self.prompts.append(str(prompt))
        self.page.solve_challenge()
        return ""

    def __enter__(self):
        self._stack.enter_context(patch("pc_automation.time", self.clock))
        self._stack.enter_context(patch("builtins.input", side_effect=self.ask))
        self._stack.enter_context(patch("operator_alert.beep", side_effect=self.beep))
        self._stack.enter_context(
            patch("operator_alert.enter_pressed", side_effect=self.enter_pressed)
        )
        self._stack.enter_context(
            patch(
                "operator_alert.discard_pending_input",
                side_effect=self.discard_pending_input,
            )
        )
        return self

    def __exit__(self, *exc_info):
        return self._stack.__exit__(*exc_info)


class SignupRun:
    """The outcome of one create_account() call against a scripted page."""

    def __init__(self, ok, automation, page, operator, logs):
        self.ok = ok
        self.automation = automation
        self.page = page
        self.operator = operator
        self.prompts = operator.prompts
        self.alerts = operator.alerts
        self.logs = logs
        self.failures = [
            call.args[0] for call in automation._save_failure.call_args_list
        ]


class SignupFlowTests(unittest.TestCase):
    """Sign-up control flow against the scripted Replit pages (virtual time).

    The page doubles follow the screenshots: the landing page hydrates after the
    document loads, "Create account" opens a chooser modal, "Continue with Email"
    swaps in the credentials form, and Replit's own checks print "Email is
    available" / "Password is valid" a moment after both fields are filled.
    """

    EMAIL = "mail@example.test"

    def setUp(self):
        self.clock = VirtualClock()

    def signup(self, enter_after_s=None, **page_options):
        # A person finishes a challenge a moment after it shows, unless the test
        # says otherwise (human_s=None: nobody does; enter_after_s: only ENTER).
        if (
            page_options.get("challenge")
            and "human_s" not in page_options
            and enter_after_s is None
        ):
            page_options["human_s"] = 2.0
        page = FakeReplitSignup(self.clock, **page_options)
        automation = PCAutomation(self.EMAIL, "S3cret-pass")
        automation.page = page
        logs = []
        automation.log = lambda message, color=None: logs.append(str(message))
        automation._save_failure = Mock()
        operator = OperatorDoubles(self.clock, page, enter_after_s=enter_after_s)
        with operator:
            ok = automation.create_account()
        return SignupRun(ok, automation, page, operator, logs)

    # -- the happy path ---------------------------------------------------- #

    def test_walks_landing_chooser_form_and_submits_after_validation(self):
        run = self.signup(validation_s=3.0)
        page = run.page

        self.assertTrue(run.ok)
        self.assertEqual(run.failures, [])
        self.assertEqual(
            [label for label in page.event_labels() if not label.startswith("fill")],
            ["chooser opened", "form opened", "submit", "result shown"],
        )
        self.assertEqual(page.email.value, self.EMAIL)
        self.assertEqual(page.password.value, "S3cret-pass")
        # Replit's own checks had finished when the button was pressed.
        self.assertGreaterEqual(page.submit_at, page.validated_at())
        self.assertEqual(page.submit.clicks, 1)
        self.assertEqual(page.create_account.clicks, 1)
        self.assertEqual(page.email_option.clicks, 1)

    def test_the_permanent_recaptcha_notice_never_pauses_the_run(self):
        run = self.signup()

        self.assertTrue(run.ok)
        self.assertIn("reCAPTCHA", run.page.body_text())
        self.assertEqual(run.prompts, [])
        self.assertEqual(run.alerts, [])
        self.assertEqual(run.page.front_calls, 0)

    def test_a_marker_that_only_says_the_field_is_valid_is_not_an_error(self):
        run = self.signup(marker_text="Password is valid")

        self.assertTrue(run.ok)
        self.assertEqual(run.failures, [])

    # -- reaching the form ------------------------------------------------- #

    def test_late_hydration_is_waited_out_instead_of_failing(self):
        run = self.signup(hydrate_s=8.0)

        self.assertTrue(run.ok)
        self.assertEqual(run.page.goto_urls, [PCAutomation.REPLIT_URL])
        self.assertGreaterEqual(self.clock.elapsed, 8.0)

    def test_a_landing_page_that_never_renders_uses_the_deep_link_once(self):
        run = self.signup(hydrate_s=None, deep_link="chooser")

        self.assertTrue(run.ok)
        self.assertEqual(
            run.page.goto_urls,
            [PCAutomation.REPLIT_URL, PCAutomation.SIGNUP_DEEP_LINK],
        )

    def test_no_form_fails_with_evidence_after_the_form_wait(self):
        run = self.signup(hydrate_s=None, deep_link="nothing")

        self.assertFalse(run.ok)
        self.assertEqual(run.failures, ["signup_form_not_found"])
        self.assertEqual(
            run.page.goto_urls,
            [PCAutomation.REPLIT_URL, PCAutomation.SIGNUP_DEEP_LINK],
        )
        self.assertGreaterEqual(
            self.clock.elapsed, PCAutomation.SIGNUP_FORM_WAIT_MS / 1_000
        )
        self.assertLess(self.clock.elapsed, 60)

    def test_an_unclickable_control_is_tapped_a_bounded_number_of_times(self):
        run = self.signup(
            click_errors={"create_account": RuntimeError("covered by an overlay")},
            deep_link="chooser",
        )

        self.assertTrue(run.ok)  # the deep link took over
        self.assertEqual(
            run.page.create_account.click_attempts, PCAutomation.CONTROL_TAP_LIMIT
        )

    def test_the_header_button_behind_an_open_modal_is_never_pressed(self):
        # The chooser opens but offers no e-mail option. The header "Create
        # account" button is still on screen behind it; pressing it (or the
        # modal's own submit) from here would be a stray click.
        run = self.signup(email_option=False)

        self.assertFalse(run.ok)
        self.assertEqual(run.failures, ["signup_form_not_found"])
        self.assertEqual(run.page.create_account.clicks, 1)  # the legitimate one
        self.assertNotIn(
            "header button pressed behind a modal", run.page.event_labels()
        )

    # -- typing and validating --------------------------------------------- #

    def test_values_the_form_throws_away_are_typed_again(self):
        run = self.signup(field_wipes=1)

        self.assertTrue(run.ok)
        self.assertEqual(run.page.email.fills, 2)
        self.assertEqual(run.page.password.fills, 2)
        self.assertEqual(run.page.email.value, self.EMAIL)

    def test_a_form_that_keeps_nothing_is_a_recorded_failure(self):
        run = self.signup(fields_keep_value=False)

        self.assertFalse(run.ok)
        self.assertEqual(run.failures, ["signup_fill_failed"])
        self.assertEqual(run.page.submit.clicks, 0)

    def test_the_submit_button_waits_for_the_validation_messages(self):
        run = self.signup(validation_s=6.0)

        self.assertTrue(run.ok)
        self.assertGreaterEqual(run.page.submit_at - run.page.filled_at, 6.0)

    def test_a_validation_error_stops_before_anything_is_submitted(self):
        run = self.signup(validation_error="Email already in use")

        self.assertFalse(run.ok)
        self.assertEqual(run.failures, ["signup_validation_before_submit"])
        self.assertEqual(run.page.submit.clicks, 0)

    def test_validation_that_never_finishes_submits_anyway_and_says_so(self):
        run = self.signup(validation_s=None)

        self.assertTrue(run.ok)
        self.assertGreaterEqual(
            run.page.submit_at - run.page.filled_at,
            PCAutomation.SIGNUP_VALIDATION_WAIT_MS / 1_000,
        )
        self.assertTrue(
            any("validation messages were not seen" in line for line in run.logs)
        )

    # -- challenges (always manual) ---------------------------------------- #

    def test_a_challenge_after_submit_pauses_once_then_the_result_is_observed(self):
        for kind in ("text", "frame"):
            with self.subTest(challenge=kind):
                self.clock = VirtualClock()
                run = self.signup(challenge=kind, challenge_when="submit")

                self.assertTrue(run.ok)
                # One pause: one sound, one trip to the front, no ENTER needed.
                self.assertEqual(len(run.alerts), 1)
                self.assertEqual(run.page.front_calls, 1)
                self.assertEqual(run.prompts, [])
                self.assertEqual(run.page.challenge_solves, 1)
                self.assertEqual(run.failures, [])

    def test_a_challenge_before_submit_is_handled_before_the_click(self):
        # A person who takes longer than the validation wait: the run is paused
        # on it before it presses the submit button.
        run = self.signup(challenge="text", challenge_when="fill", human_s=10.0)

        self.assertTrue(run.ok)
        labels = run.page.event_labels()
        self.assertLess(labels.index("challenge solved"), labels.index("submit"))
        self.assertEqual(len(run.alerts), 1)
        self.assertEqual(run.prompts, [])

    def test_a_challenge_finished_before_the_run_looked_causes_no_pause(self):
        # Done within the validation wait: nothing is left to pause on, so no
        # sound and no window is pulled forward.
        run = self.signup(challenge="text", challenge_when="fill", human_s=1.0)

        self.assertTrue(run.ok)
        self.assertEqual(run.page.challenge_solves, 1)
        self.assertEqual(run.alerts, [])
        self.assertEqual(run.page.front_calls, 0)
        self.assertEqual(run.prompts, [])

    def test_a_challenge_on_the_landing_page_is_handled_before_any_click(self):
        run = self.signup(challenge="frame", challenge_when="load")

        self.assertTrue(run.ok)
        labels = run.page.event_labels()
        self.assertLess(labels.index("challenge solved"), labels.index("chooser opened"))
        self.assertEqual(len(run.alerts), 1)

    def test_an_unsolved_challenge_fails_with_evidence_and_never_retries(self):
        # Nobody finishes it; the person presses ENTER anyway.
        run = self.signup(
            challenge="frame",
            challenge_when="submit",
            solvable=False,
            enter_after_s=3.0,
        )

        self.assertFalse(run.ok)
        self.assertEqual(run.failures, ["captcha_unresolved"])
        self.assertEqual(len(run.alerts), 1)
        self.assertEqual(run.page.submit.clicks, 1)

    def test_a_challenge_that_keeps_returning_is_bounded(self):
        # Every round is solved, but a new one shows up well after the run had
        # carried on (4 s, longer than the settle window), so each is a new pause.
        run = self.signup(
            challenge="frame",
            challenge_when="submit",
            challenge_repeats=20,
            rearm_after_s=4.0,
            human_s=1.0,
        )

        self.assertFalse(run.ok)
        self.assertEqual(len(run.alerts), PCAutomation.MAX_MANUAL_CHALLENGES)
        self.assertEqual(run.page.front_calls, PCAutomation.MAX_MANUAL_CHALLENGES)
        self.assertEqual(run.prompts, [])
        self.assertEqual(run.failures, ["signup_challenge_repeated"])
        self.assertEqual(run.page.submit.clicks, 1)

    # -- what counts as a result ------------------------------------------- #

    def test_no_visible_result_is_a_timeout_failure(self):
        run = self.signup(outcome="nothing")

        self.assertFalse(run.ok)
        self.assertEqual(run.failures, ["signup_result_timeout"])
        self.assertGreaterEqual(
            self.clock.elapsed, PCAutomation.SIGNUP_RESULT_WAIT_MS / 1_000
        )

    def test_a_visible_error_after_submit_is_reported(self):
        run = self.signup(outcome="error")

        self.assertFalse(run.ok)
        self.assertEqual(run.failures, ["signup_validation_after_submit"])

    def test_a_submit_control_that_cannot_be_pressed_is_an_exception_failure(self):
        run = self.signup(click_errors={"submit": RuntimeError("detached")})

        self.assertFalse(run.ok)
        self.assertEqual(run.failures, ["signup_exception"])

    # -- the optional one-tap pop-up --------------------------------------- #

    def test_the_google_popup_is_closed_when_it_can_be(self):
        run = self.signup(popup="closes")

        self.assertTrue(run.ok)
        self.assertIn("popup closed", run.page.event_labels())

    def test_a_popup_that_cannot_be_closed_does_not_abort_sign_up(self):
        run = self.signup(popup="close_fails")

        self.assertTrue(run.ok)
        self.assertEqual(run.failures, [])


class ChallengeWaitTests(unittest.TestCase):
    """handle_captcha(): the run gets the person's attention, then notices.

    Nothing here solves a challenge. The doubles only model when a person does
    (``human_s``) and what the program does meanwhile: a sound, the window to
    the front, a gentle poll of the page, and carrying on once the challenge has
    stayed gone. ENTER in the terminal stays as an override.
    """

    SETTLE_S = PCAutomation.CHALLENGE_SETTLE_MS / 1_000

    def wait(
        self,
        *,
        show=True,
        enter_after_s=None,
        beep_error=None,
        front_error=None,
        **page_options,
    ):
        page_options.setdefault("challenge", "frame")
        clock = VirtualClock()
        page = FakeReplitSignup(clock, **page_options)
        if front_error is not None:
            page.bring_to_front = Mock(side_effect=front_error)
        polls = []
        real_wait = page.wait_for_timeout

        def recording_wait(milliseconds):
            polls.append(milliseconds)
            real_wait(milliseconds)

        page.wait_for_timeout = recording_wait
        automation = PCAutomation("mail@example.test", "S3cret-pass")
        automation.page = page
        logs = []
        automation.log = lambda message, color=None: logs.append(str(message))
        automation._save_failure = Mock()
        operator = OperatorDoubles(clock, page, enter_after_s, beep_error)
        if show:
            page.show_challenge()
        with operator:
            ok = automation.handle_captcha()
        return SimpleNamespace(
            ok=ok,
            clock=clock,
            page=page,
            operator=operator,
            prompts=operator.prompts,
            alerts=operator.alerts,
            logs=logs,
            polls=polls,
            failures=[c.args[0] for c in automation._save_failure.call_args_list],
        )

    def solved_at(self, run):
        return [t for t, label in run.page.events if label == "challenge solved"][-1]

    # -- the normal way: do it in the browser, the run notices ------------- #

    def test_it_carries_on_by_itself_once_the_challenge_is_gone(self):
        for kind in ("text", "frame"):
            with self.subTest(challenge=kind):
                run = self.wait(challenge=kind, human_s=3.0)

                self.assertTrue(run.ok)
                self.assertEqual(run.prompts, [])  # no ENTER needed
                self.assertEqual(len(run.alerts), 1)
                self.assertTrue(any("Resuming" in line for line in run.logs))
                # ...but only after it has stayed gone for the settle window.
                solved = self.solved_at(run)
                self.assertGreaterEqual(run.clock.elapsed, solved + self.SETTLE_S)
                self.assertLessEqual(
                    run.clock.elapsed,
                    solved + self.SETTLE_S + PCAutomation.CHALLENGE_POLL_MS / 1_000,
                )

    def test_the_sound_and_the_window_come_once_when_the_pause_starts(self):
        run = self.wait(human_s=5.0)

        self.assertEqual(len(run.alerts), 1)
        self.assertEqual(run.alerts[0], run.clock.start)  # before any waiting
        self.assertEqual(run.page.front_calls, 1)

    def test_it_says_plainly_that_a_person_has_to_do_it(self):
        run = self.wait(human_s=2.0)

        text = "\n".join(run.logs)
        self.assertIn("will not solve or retry it", text)
        self.assertIn("press ENTER", text)

    def test_a_challenge_that_flickers_between_rounds_is_one_pause(self):
        run = self.wait(human_s=1.0, challenge_repeats=1, rearm_after_s=1.0)

        self.assertTrue(run.ok)
        self.assertEqual(run.page.challenge_solves, 2)  # both rounds were finished
        self.assertFalse(run.page.challenge_active)  # the run waited for the second
        self.assertEqual(len(run.alerts), 1)  # without a second alarm
        self.assertEqual(run.page.front_calls, 1)

    def test_the_sound_repeats_as_a_reminder_but_the_window_is_not_pulled_again(self):
        run = self.wait(human_s=130.0)

        self.assertTrue(run.ok)
        self.assertEqual(len(run.alerts), 3)  # at the start, then at 60 s and 120 s
        gaps = [later - earlier for earlier, later in zip(run.alerts, run.alerts[1:])]
        self.assertTrue(all(gap >= PCAutomation.CHALLENGE_REMIND_S for gap in gaps))
        self.assertEqual(run.page.front_calls, 1)
        self.assertEqual(run.prompts, [])
        self.assertTrue(any("Still waiting" in line for line in run.logs))

    def test_the_page_is_polled_gently(self):
        run = self.wait(human_s=20.0)

        self.assertTrue(run.polls)
        self.assertTrue(all(ms >= 1_000 for ms in run.polls))

    # -- ENTER stays as an override ---------------------------------------- #

    def test_enter_skips_the_settle_window(self):
        plain = self.wait(human_s=2.0)
        pressed = self.wait(human_s=2.0, enter_after_s=2.0)

        self.assertTrue(plain.ok and pressed.ok)
        self.assertLess(pressed.clock.elapsed, plain.clock.elapsed)
        self.assertLess(pressed.clock.elapsed, 2.0 + self.SETTLE_S)
        self.assertEqual(pressed.prompts, [])

    def test_enter_while_the_challenge_is_still_there_is_not_taken_on_trust(self):
        run = self.wait(solvable=False, enter_after_s=3.0)

        self.assertFalse(run.ok)
        self.assertEqual(run.failures, ["captcha_unresolved"])
        self.assertGreaterEqual(
            run.clock.elapsed, 3.0 + PCAutomation.CHALLENGE_CLEAR_WAIT_MS / 1_000
        )
        self.assertEqual(run.prompts, [])

    def test_an_old_enter_is_forgotten_before_the_wait_starts(self):
        run = self.wait(human_s=2.0)

        self.assertEqual(run.operator.calls[0], "discard")
        self.assertEqual(run.operator.calls.count("discard"), 1)
        self.assertIn("enter?", run.operator.calls)

    def test_after_a_long_wait_it_asks_for_enter_the_old_way(self):
        run = self.wait()  # nobody finishes it until asked

        self.assertTrue(run.ok)
        self.assertEqual(len(run.prompts), 1)
        self.assertIn("ENTER", run.prompts[0])
        self.assertGreaterEqual(run.clock.elapsed, PCAutomation.CHALLENGE_AUTO_WAIT_S)
        # The question comes with a sound, so it is not missed.
        self.assertGreaterEqual(
            run.alerts[-1] - run.clock.start, PCAutomation.CHALLENGE_AUTO_WAIT_S
        )

    # -- robustness --------------------------------------------------------- #

    def test_a_sound_or_window_that_fails_never_stops_the_run(self):
        run = self.wait(
            human_s=2.0,
            beep_error=OSError("no sound device"),
            front_error=RuntimeError("window is gone"),
        )

        self.assertTrue(run.ok)
        self.assertEqual(run.failures, [])
        run.page.bring_to_front.assert_called_once()
        self.assertGreaterEqual(len(run.alerts), 1)

    def test_without_a_challenge_nothing_happens(self):
        run = self.wait(show=False, challenge=None)

        self.assertTrue(run.ok)
        self.assertEqual(run.alerts, [])
        self.assertEqual(run.prompts, [])
        self.assertEqual(run.page.front_calls, 0)
        self.assertEqual(run.operator.calls, [])
        self.assertEqual(run.clock.elapsed, 0)


if __name__ == "__main__":
    unittest.main()
