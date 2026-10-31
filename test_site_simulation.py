"""Real-Chromium regression tests against local imitations of the sites.

The project's own PC flow (temp-mail address -> Replit sign-up -> open the
verification message -> follow the link) runs in a real, headless Chromium. All
traffic to replit.com / temp-mail.org is answered by ``site_simulator.Sim`` from
inside the test process and every other web request is blocked, so nothing here
touches a real site, and no challenge is ever solved by code: the "person" in
these tests acts only when the run rings its sound (operator_alert.beep), and
then finishes the simulated challenge in the browser a moment later, exactly
like someone at the keyboard would. Nobody presses ENTER: the run notices that
the challenge is gone and carries on, and a test fails if it asks for ENTER.

What this proves: selectors, waits, overlays, iframes, popups and single-use
links behave correctly in a real browser against pages that reproduce the
behaviour seen in the screenshots. What it cannot prove: that the live Replit or
temp-mail.org pages look like the imitation. A manual run is still the only
evidence for that.

Skipped automatically when patchright or a Chromium-family browser is missing,
or when AFK_SKIP_BROWSER_TESTS=1. AFK_SIM_FULL=1 adds the slower scenarios;
PLAYWRIGHT_EXECUTABLE_PATH selects the browser, like in the real tool.
"""

import contextlib
import io
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

try:
    from patchright.sync_api import sync_playwright
except Exception:  # pragma: no cover - depends on the machine
    sync_playwright = None

from pc_automation import PCAutomation
from site_simulator import Sim

FULL = os.environ.get("AFK_SIM_FULL") == "1"
SKIP = os.environ.get("AFK_SKIP_BROWSER_TESTS") == "1"

# Short latencies keep each scenario to a few seconds.
FAST = {
    "hydrate_ms": 400,
    "validate_ms": 300,
    "submit_ms": 300,
    "verify_ms": 200,
    "mail_delay_ms": 800,
}

_playwright = None
_browser = None
_skip_reason = None


def _launch(playwright):
    executables = PCAutomation("x@example.test", "pw")._browser_executable_candidates()
    attempts = [{"executable_path": path} for path in executables] + [{}]
    args = ["--no-sandbox"] if os.name != "nt" else []
    last_error = None
    for options in attempts:
        try:
            return playwright.chromium.launch(headless=True, args=args, **options)
        except Exception as exc:  # try the next candidate
            last_error = exc
    raise last_error


def setUpModule():
    global _playwright, _browser, _skip_reason
    if SKIP:
        _skip_reason = "AFK_SKIP_BROWSER_TESTS=1"
        return
    if sync_playwright is None:
        _skip_reason = "patchright is not installed"
        return
    try:
        _playwright = sync_playwright().start()
        _browser = _launch(_playwright)
    except Exception as exc:
        _skip_reason = f"no Chromium-family browser could be launched ({type(exc).__name__})"
        if _playwright is not None:
            try:
                _playwright.stop()
            except Exception:
                pass
            _playwright = None


def tearDownModule():
    global _playwright, _browser
    if _browser is not None:
        try:
            _browser.close()
        except Exception:
            pass
    if _playwright is not None:
        try:
            _playwright.stop()
        except Exception:
            pass
    _browser = _playwright = None


class FlowRun:
    def __init__(self, sim, steps, prompts, alerts, log, address):
        self.sim = sim
        self.steps = steps
        self.prompts = prompts
        self.alerts = alerts
        self.log = log
        self.address = address

    def describe(self):
        tail = "\n".join(self.log.splitlines()[-12:])
        return (
            f"steps={self.steps}\nprompts={self.prompts}\nalerts={self.alerts}\n"
            f"log tail:\n{tail}"
        )

    def verify_attempts(self, ok):
        return self.sim.count("verify_attempt", ok=ok)

    def mail_ui_events(self):
        with self.sim.lock:
            return [e.get("event") for e in self.sim.events if e["kind"] == "mail_ui"]


class SimulatedOldAccountTests(unittest.TestCase):
    """Replit opens signed in to an old account: it is deleted, then sign-up runs."""

    OLD = "old.account@example.test"
    EMAIL = "new.account@example.test"

    def setUp(self):
        if _skip_reason:
            self.skipTest(_skip_reason)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = os.path.join(self.temp.name, "ledger.json")
        env = patch.dict(os.environ, {
            "AFK_ACCOUNT_LEDGER": self.ledger,
            "AFK_EXISTING_SESSION_ACTION": "delete",
        })
        env.start()
        self.addCleanup(env.stop)

    def run_signup(self, *, known=True, answers=(), **sim_options):
        import account_ledger

        if known:
            account_ledger.record(self.OLD)
        options = dict(FAST)
        options.update(old_account=self.OLD, **sim_options)
        sim = Sim(**options)
        context = _browser.new_context(viewport={"width": 1366, "height": 700})
        sim.install(context)
        prompts, queue = [], list(answers)

        def ask(prompt=""):
            prompts.append(str(prompt))
            return queue.pop(0) if queue else ""

        output = io.StringIO()
        original_cwd = os.getcwd()
        try:
            os.chdir(self.temp.name)
            with patch("builtins.input", ask), patch("operator_alert.beep"), patch(
                "operator_alert.enter_pressed", return_value=False
            ), patch("operator_alert.discard_pending_input"), contextlib.redirect_stdout(output):
                pc = PCAutomation(self.EMAIL, "12345678")
                pc.context = context
                pc.page = context.new_page()
                pc._owns_browser = True
                ok = pc.create_account()
        finally:
            os.chdir(original_cwd)
            context.close()
        return ok, sim, prompts, output.getvalue()

    def test_the_old_account_is_deleted_through_the_settings_page_then_signup_runs(self):
        ok, sim, prompts, log = self.run_signup()
        self.assertTrue(ok, log)
        self.assertEqual(prompts, [])
        self.assertEqual(sim.errors, [])
        self.assertEqual(sim.count("blocked_request"), 0)
        self.assertEqual(sim.count("account_deleted", email=self.OLD), 1)
        self.assertEqual(sim.count("signup_submit", email=self.EMAIL), 1)
        kinds = [e["kind"] for e in sim.events]
        self.assertLess(kinds.index("account_deleted"), kinds.index("signup_submit"))
        self.assertLess(kinds.index("settings_page"), kinds.index("account_deleted"))

    def test_a_slow_redirect_and_a_slow_deletion_are_waited_for(self):
        ok, sim, _prompts, log = self.run_signup(redirect_ms=3000, delete_ms=4000)
        self.assertTrue(ok, log)
        self.assertEqual(sim.count("account_deleted"), 1)
        self.assertEqual(sim.count("signup_submit"), 1)

    def test_an_account_the_tool_did_not_create_is_deleted_without_asking(self):
        # No confirmation prompt: an account missing from the ledger goes the same way.
        ok, sim, prompts, log = self.run_signup(known=False)
        self.assertTrue(ok, log)
        self.assertEqual(prompts, [])
        self.assertNotIn("About to DELETE", log)
        self.assertEqual(sim.count("account_deleted", email=self.OLD), 1)
        self.assertEqual(sim.count("signup_submit", email=self.EMAIL), 1)


class SimulatedImportPageTests(unittest.TestCase):
    """replit.com/import/github (attached_assets/import_from_github_page.png) in a real browser.

    The "GitHub Repo URL" box is found and filled, and nothing on the page is
    clicked: not "Connect your GitHub account", not the "Import from GitHub"
    button that appears once a link is in the box.
    """

    SIGNED_IN = "signed.in@example.test"
    LINK = "https://github.com/someone/some-repo"

    def setUp(self):
        if _skip_reason:
            self.skipTest(_skip_reason)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    @contextlib.contextmanager
    def session(self, **sim_options):
        options = dict(FAST)
        options.update(sim_options)
        sim = Sim(**options)
        context = _browser.new_context(viewport={"width": 1366, "height": 700})
        sim.install(context)
        output = io.StringIO()
        original_cwd = os.getcwd()
        try:
            os.chdir(self.temp.name)  # failure screenshots land here, not in the project
            with patch("operator_alert.beep"), contextlib.redirect_stdout(output):
                pc = PCAutomation("new.account@example.test", "12345678")
                pc.context = context
                pc.page = context.new_page()
                pc._owns_browser = True
                yield sim, pc, output
        finally:
            os.chdir(original_cwd)
            context.close()

    @staticmethod
    def typed_values(sim):
        with sim.lock:
            return [
                e.get("value")
                for e in sim.events
                if e["kind"] == "replit_ui" and e.get("event") == "import_url_typed"
            ]

    def test_the_page_opens_and_the_link_is_typed_into_the_box_without_clicking_anything(self):
        with self.session(old_account=self.SIGNED_IN) as (sim, pc, output):
            self.assertTrue(pc.open_github_import_page(), output.getvalue())
            self.assertTrue(pc.enter_github_repo_url(self.LINK), output.getvalue())
            self.assertEqual(pc.page.input_value("#repo-url"), self.LINK)
            # the page reacted like the real one: an Import button is now offered...
            self.assertTrue(pc.page.locator("#do-import").is_visible())
            pc.page.wait_for_timeout(600)  # let the page's own events reach the simulator
            self.assertEqual(sim.errors, [])
            self.assertEqual(sim.count("blocked_request"), 0)
            self.assertEqual(sim.count("import_page", email=self.SIGNED_IN), 1)
            self.assertEqual(self.typed_values(sim)[-1], self.LINK)
            # ...and nobody pressed it, nor the GitHub connect button
            self.assertEqual(sim.count("replit_ui", event="import_clicked"), 0)
            self.assertEqual(sim.count("replit_ui", event="connect_clicked"), 0)
            self.assertIn(self.LINK, output.getvalue())

    def test_a_box_that_renders_late_is_waited_for(self):
        with self.session(old_account=self.SIGNED_IN, hydrate_ms=3500) as (sim, pc, output):
            self.assertTrue(pc.open_github_import_page(), output.getvalue())
            self.assertTrue(pc.enter_github_repo_url(self.LINK), output.getvalue())
            self.assertEqual(pc.page.input_value("#repo-url"), self.LINK)

    def test_when_nobody_is_signed_in_replit_sends_the_tab_to_login_and_nothing_is_typed(self):
        with self.session(old_account="", redirect_ms=100) as (sim, pc, output):
            self.assertFalse(pc.open_github_import_page(), output.getvalue())
            self.assertIn("/login", pc.page.url)
            started = time.monotonic()
            self.assertFalse(pc.enter_github_repo_url(self.LINK))
            self.assertLess(time.monotonic() - started, 10)  # no 30 second wait on the login page
            self.assertEqual(sim.count("import_page"), 0)
            self.assertEqual(sim.count("import_page_signed_out"), 1)
            self.assertEqual(self.typed_values(sim), [])
            self.assertIn("instead of the import page", output.getvalue())
            self.assertIn(self.LINK, output.getvalue())  # printed so it can be pasted by hand

    def test_a_redirect_that_comes_a_second_or_two_after_the_load_is_still_noticed(self):
        with self.session(old_account="", redirect_ms=1800) as (sim, pc, output):
            self.assertFalse(pc.open_github_import_page(), output.getvalue())
            self.assertIn("/login", pc.page.url)

    def test_a_box_that_never_appears_gives_the_link_back(self):
        with patch.object(PCAutomation, "GITHUB_IMPORT_LOAD_WAIT_MS", 1500), \
                patch.object(PCAutomation, "GITHUB_IMPORT_SETTLE_MS", 1000):
            with self.session(old_account=self.SIGNED_IN, hydrate_ms=60_000) as (sim, pc, output):
                self.assertTrue(pc.open_github_import_page())
                self.assertFalse(pc.enter_github_repo_url(self.LINK))
                self.assertEqual(self.typed_values(sim), [])
                self.assertIn("Paste the link yourself", output.getvalue())
                self.assertIn(self.LINK, output.getvalue())


class SimulatedSiteFlowTests(unittest.TestCase):
    def setUp(self):
        if _skip_reason:
            self.skipTest(_skip_reason)

    def run_flow(self, *, mail_timeout=30, lose_mailbox=False, before_signup=None,
                 after_verify=None, **sim_options):
        options = dict(FAST)
        options.update(sim_options)
        sim = Sim(**options)
        context = _browser.new_context(viewport={"width": 1366, "height": 700})
        sim.install(context)
        prompts = []
        alerts = []
        timers = []

        def ask(prompt=""):
            """A blocking ENTER prompt. Nobody should be asked for one here."""

            prompts.append(str(prompt))
            return ""

        def sound():
            """The run rang for a person, who finishes the challenge in the browser."""

            alerts.append(round(time.monotonic() - sim.t0, 3))
            if sim.cfg["captcha"] != "none":
                timer = threading.Timer(0.4, sim.solve_captcha)
                timer.daemon = True
                timers.append(timer)
                timer.start()

        steps = {}
        output = io.StringIO()
        original_cwd = os.getcwd()
        address = None
        try:
            with tempfile.TemporaryDirectory() as workdir:
                os.chdir(workdir)
                try:
                    with patch("builtins.input", ask), patch(
                        "operator_alert.beep", sound
                    ), patch("operator_alert.enter_pressed", return_value=False), patch(
                        "operator_alert.discard_pending_input"
                    ), contextlib.redirect_stdout(output):
                        pc = PCAutomation(
                            None,
                            "12345678",
                            email_strategy="temp-mail.org",
                            email_check_timeout=mail_timeout,
                        )
                        pc.context = context
                        pc.page = context.new_page()
                        pc._owns_browser = True

                        def step(name, call):
                            try:
                                steps[name] = {"ok": bool(call())}
                            except Exception as exc:
                                steps[name] = {
                                    "ok": False,
                                    "error": f"{type(exc).__name__}: {exc}",
                                }
                            return steps[name]["ok"]

                        address = pc.obtain_temp_email()
                        steps["email"] = {"ok": bool(address)}
                        if before_signup is not None:
                            before_signup(pc)
                        if step("signup", pc.create_account):
                            if lose_mailbox:
                                # A new session of the mailbox site shows another address.
                                sim.new_mailbox()
                                pc.email_provider.page.reload(wait_until="domcontentloaded")
                            if step("open_message", pc.open_verification_message):
                                if step("verify", pc.verify_email) and after_verify:
                                    step("after", lambda: after_verify(pc))
                finally:
                    os.chdir(original_cwd)
        finally:
            for timer in timers:
                timer.cancel()
            context.close()
        return FlowRun(sim, steps, prompts, alerts, output.getvalue(), address)

    def assert_clean_success(self, run):
        details = run.describe()
        for name in ("email", "signup", "open_message", "verify"):
            self.assertTrue(run.steps.get(name, {}).get("ok"), f"{name} failed\n{details}")
        # The verification link is single-use: exactly one visit, and it worked.
        self.assertEqual(run.verify_attempts(True), 1, details)
        self.assertEqual(run.verify_attempts(False), 0, details)
        # Nobody is ever asked to press ENTER; a challenge round trip is one
        # extra submit, sent by the page itself once the challenge is gone.
        self.assertEqual(run.prompts, [], details)
        self.assertEqual(run.sim.count("signup_submit"), 1 if not run.alerts else 2, details)
        self.assertEqual(run.sim.errors, [], details)
        self.assertEqual(run.sim.count("blocked_request"), 0, details)
        # Nothing but the expected mailbox buttons was pressed.
        self.assertTrue(
            set(run.mail_ui_events()) <= {"copy", "refresh", "message_opened", "delete"},
            f"{run.mail_ui_events()}\n{details}",
        )

    # -- default scenarios -------------------------------------------------- #

    def test_the_whole_flow_succeeds_and_follows_the_link_once(self):
        run = self.run_flow()

        self.assert_clean_success(run)
        # The reCAPTCHA legal notice in every modal must not pause the run.
        self.assertEqual(run.alerts, [], run.describe())

    def test_a_slow_page_with_table_rows_and_an_inline_message_still_works(self):
        # Late hydration, a form that ignores clicks while it validates, an
        # inbox of table rows and a message body printed in the page itself.
        run = self.run_flow(
            hydrate_ms=2500,
            validate_ms=700,
            early_submit="ignore",
            rows="table",
            message_view="inline",
        )

        self.assert_clean_success(run)
        self.assertEqual(run.alerts, [], run.describe())
        self.assertEqual(run.sim.count("replit_ui", event="submit_ignored_pending"), 0)

    def test_a_challenge_iframe_is_paused_for_and_the_flow_then_resumes(self):
        run = self.run_flow(captcha="frame")

        self.assert_clean_success(run)
        self.assertEqual(len(run.alerts), 1, run.describe())
        self.assertEqual(run.sim.count("challenge_shown"), 1)

    def test_a_sign_up_dialog_the_person_already_opened_is_continued_not_reloaded(self):
        def open_the_dialog_by_hand(pc):
            page = pc.context.new_page()  # the person's own Replit tab
            page.goto("https://replit.com/?authModal=signup", wait_until="domcontentloaded")
            page.wait_for_selector("text=Continue with Email", timeout=10_000)

        run = self.run_flow(before_signup=open_the_dialog_by_hand)

        self.assert_clean_success(run)
        # One page load: the person's. The run read the page and carried on.
        self.assertEqual(run.sim.count("replit_page_load"), 1, run.describe())
        self.assertIn("Continuing on the sign-up page that is already open", run.log)

    def test_the_temp_mailbox_is_deleted_after_verification_and_only_then(self):
        run = self.run_flow(after_verify=lambda pc: pc.discard_mailbox())

        self.assert_clean_success(run)
        self.assertTrue(run.steps["after"]["ok"], run.describe())
        self.assertEqual(run.mail_ui_events().count("delete"), 1, run.describe())
        self.assertEqual(len(run.sim.mailboxes), 2, "a fresh address replaces the deleted one")
        self.assertEqual(run.verify_attempts(True), 1)  # the link was followed before

    def test_another_mailbox_on_the_page_is_never_deleted(self):
        holder = {}
        original = Sim.__init__

        def capture(sim, *args, **kwargs):
            original(sim, *args, **kwargs)
            holder["sim"] = sim

        def swap_mailbox_then_delete(pc):
            holder["sim"].new_mailbox()  # the page now shows an address that is not ours
            pc.email_provider._mailbox_tab().reload(wait_until="domcontentloaded")
            return pc.discard_mailbox()

        with patch.object(Sim, "__init__", capture):
            run = self.run_flow(after_verify=swap_mailbox_then_delete)

        self.assertEqual(run.steps["after"], {"ok": False}, run.describe())
        self.assertEqual(run.mail_ui_events().count("delete"), 0)

    # -- slower scenarios (AFK_SIM_FULL=1) ----------------------------------- #

    @unittest.skipUnless(FULL, "set AFK_SIM_FULL=1 for the full simulator matrix")
    def test_a_text_challenge_is_paused_for_and_the_flow_then_resumes(self):
        run = self.run_flow(captcha="text")

        self.assert_clean_success(run)
        self.assertEqual(len(run.alerts), 1, run.describe())

    @unittest.skipUnless(FULL, "set AFK_SIM_FULL=1 for the full simulator matrix")
    def test_page_variants_do_not_matter(self):
        variants = (
            {"badge": "visible"},          # the invisible-reCAPTCHA badge drawn on screen
            {"footer_email": True},        # another e-mail box earlier in the page
            {"email_type": "text"},        # the e-mail input is not type=email
            {"show_notice": False},        # no legal notice at all
            {"rows": "table"},             # table rows, iframe message
            {"message_view": "inline"},    # div rows, inline message
        )
        for options in variants:
            with self.subTest(options=options):
                run = self.run_flow(**options)
                self.assert_clean_success(run)
                self.assertEqual(run.alerts, [], run.describe())

    @unittest.skipUnless(FULL, "set AFK_SIM_FULL=1 for the full simulator matrix")
    def test_a_message_that_needs_the_refresh_button_is_found(self):
        run = self.run_flow(auto_refresh=False, mail_delay_ms=3000)

        self.assert_clean_success(run)
        self.assertIn("refresh", run.mail_ui_events())

    @unittest.skipUnless(FULL, "set AFK_SIM_FULL=1 for the full simulator matrix")
    def test_a_lost_mailbox_session_is_diagnosed_instead_of_waiting_forever(self):
        run = self.run_flow(lose_mailbox=True, mail_timeout=6)

        self.assertTrue(run.steps["signup"]["ok"], run.describe())
        failure = run.steps["open_message"]
        self.assertFalse(failure["ok"], run.describe())
        self.assertIn("mailbox session", failure["error"], run.describe())
        self.assertIn(run.address, failure["error"], run.describe())
        self.assertNotIn("verify", run.steps)


if __name__ == "__main__":
    unittest.main()
