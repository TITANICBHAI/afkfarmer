import os
import re
import shlex
import time
from typing import Optional
from urllib.parse import urlsplit
from urllib.request import urlopen

from patchright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)
from colorama import Fore, Style

import account_ledger
import browser_locator
import operator_alert
from email_provider import EmailProvider
from email_providers import create_email_provider
from pc_flow import (
    classify_replit_page,
    classify_session_state,
    classify_signup_form_text,
    classify_signup_state,
    find_validation_errors,
    has_security_challenge_text,
    is_profile_settings_text,
    is_workspace_url,
    parse_account_identity,
)
from temp_mail import TempMailOrgProvider


def _setting(name: str, default):
    """A setting from the environment (AFK_<NAME>), else config.py, else ``default``."""

    value = os.environ.get(f"AFK_{name}")
    if value not in (None, ""):
        return value
    try:
        import config

        return getattr(config, name, default)
    except Exception:
        return default


class PCAutomation:
    REPLIT_URL = "https://replit.com/"
    SIGNUP_URL = "https://replit.com/signup"
    # Opens the sign-up modal directly (seen in attached_assets/IMG-20260922-WA0000).
    SIGNUP_DEEP_LINK = "https://replit.com/?authModal=signup"
    SIGNUP_RESULT_WAIT_MS = 60_000
    SIGNUP_FORM_WAIT_MS = 45_000        # landing page -> credentials form
    SIGNUP_ENTRY_PATIENCE_MS = 12_000   # then fall back to the deep link once
    SIGNUP_VALIDATION_WAIT_MS = 15_000  # "Email is available" / "Password is valid"
    CHALLENGE_CLEAR_WAIT_MS = 10_000    # grace after the operator reports it solved
    CHALLENGE_POLL_MS = 1_000           # page check interval while a person works on a challenge
    CHALLENGE_SETTLE_MS = 2_500         # it must stay gone this long before the run carries on
    CHALLENGE_REMIND_S = 60             # the sound repeats this often while waiting
    CHALLENGE_AUTO_WAIT_S = 900         # after this, ask for ENTER the old way
    MAX_MANUAL_CHALLENGES = 3           # manual pauses per sign-up attempt
    CONTROL_TAP_LIMIT = 3               # clicks on one control before giving up on it
    # Removing an old account that is still signed in (attached_assets/step_2.1a-e)
    SETTINGS_PROFILE_URL = "https://replit.com/settings/profile"
    HOME_SETTLE_WAIT_MS = 10_000        # replit.com decides: landing page or the signed-in /~
    SETTINGS_LOAD_WAIT_MS = 30_000      # Settings > Profile appears
    SETTINGS_EMAIL_WAIT_MS = 6_000      # its e-mail line fills in a moment after the labels
    DELETE_STEP_WAIT_MS = 15_000        # the delete button / the confirmation dialog appear
    LOGGED_OUT_WAIT_MS = 90_000         # after confirming: back to the logged-out home page
    # The "Import from GitHub" page (attached_assets/import_from_github_page.png)
    GITHUB_IMPORT_URL = "https://replit.com/import/github"
    GITHUB_IMPORT_SETTLE_MS = 4_000      # watch this long for a redirect (login) or the rendered page
    GITHUB_IMPORT_LOAD_WAIT_MS = 30_000  # until its "GitHub Repo URL" box shows (or Replit redirects away)
    GITHUB_IMPORT_POLL_MS = 250
    POLL_MS = 250

    # Challenge widgets only. Sites using reCAPTCHA Enterprise also inject an
    # *invisible* badge (an iframe titled "reCAPTCHA", size=invisible, inside
    # ".grecaptcha-badge"). Matching the badge, or any class containing
    # "captcha", would report a challenge on every page and block sign-up.
    CAPTCHA_SELECTORS = (
        "iframe[src*='recaptcha'][src*='bframe']",
        "iframe[title*='recaptcha challenge' i]",
        "iframe[src*='recaptcha'][src*='anchor']:not([src*='size=invisible'])",
        "iframe[src*='hcaptcha.com']",
        "iframe[src*='challenges.cloudflare.com']",
        "iframe[src*='arkoselabs'], iframe[src*='funcaptcha']",
        "[data-sitekey]:not([data-size='invisible'])",
        "[id*='captcha' i]:not([class*='grecaptcha' i])",
    )
    VALIDATION_SELECTORS = (
        "[role='alert']",
        "[aria-invalid='true']",
        "[data-testid*='error' i]",
    )

    # Sign-up entry points, tried in the order the screenshots show them:
    # header "Create account" -> chooser modal "Continue with Email" -> form.
    CREATE_ACCOUNT_SELECTORS = (
        "header button:has-text('Create account')",
        "button:has-text('Create account')",
        "button:has-text('Sign up')",
        "a:has-text('Sign up')",
    )
    EMAIL_CHOICE_SELECTORS = (
        "[role='dialog'] button:has-text('Continue with Email')",
        "button:has-text('Continue with Email')",
        "button:has-text('with Email')",
    )
    PASSWORD_INPUT = (
        "input[type='password'], input[name='password'], "
        "input[autocomplete='new-password']"
    )
    EMAIL_INPUT = (
        "input[type='email'], input[name='email'], input[autocomplete='email'], "
        "input[autocomplete='username'], input[placeholder*='email' i], "
        "input[aria-label*='email' i]"
    )
    # The password field is unique to the sign-up form, so it anchors the
    # container; the e-mail field is then looked up inside that same container
    # (never an unrelated e-mail box elsewhere on the landing page).
    PASSWORD_FIELD_SELECTORS = (
        f"[role='dialog'] :is({PASSWORD_INPUT})",
        f"dialog :is({PASSWORD_INPUT})",
        f"form :is({PASSWORD_INPUT})",
        f":is({PASSWORD_INPUT})",
    )
    EMAIL_FIELD_SELECTORS = (
        f"[role='dialog']:has({PASSWORD_INPUT}) :is({EMAIL_INPUT})",
        f"dialog:has({PASSWORD_INPUT}) :is({EMAIL_INPUT})",
        f"form:has({PASSWORD_INPUT}) :is({EMAIL_INPUT})",
        f":is({EMAIL_INPUT})",
    )
    REQUEST_DELETION_SELECTORS = (
        "button:has-text('Request account deletion')",
        "[role='button']:has-text('Request account deletion')",
    )
    CONFIRM_DELETION_SELECTORS = (
        "[role='dialog'] button:has-text('Yes, Delete my Account')",
        "button:has-text('Yes, Delete my Account')",
    )
    SUBMIT_SELECTORS = (
        "[role='dialog'] form button[type='submit']",
        "form button[type='submit']",
        "[role='dialog'] button[type='submit']",
        "button[type='submit']",
        "[role='dialog'] button:has-text('Create Account')",
        "form button:has-text('Create Account')",
        "[role='dialog'] button:has-text('Create account')",
        "form button:has-text('Create account')",
    )

    def __init__(
        self,
        email: Optional[str],
        password: str,
        temp_mail_provider: str = "temp-mail.org",
        temp_mail_url: str = TempMailOrgProvider.URL,
        email_strategy: str = "temp-mail.org",
        primary_email_api: str = "1secmail",
        user_custom_email: str = "",
        email_check_timeout: float = 60,
    ):
        self.email = email
        self.password = password
        self.temp_mail_provider = temp_mail_provider
        self.temp_mail_url = temp_mail_url
        self.email_strategy = email_strategy
        self.primary_email_api = primary_email_api
        self.user_custom_email = user_custom_email
        self.email_check_timeout = email_check_timeout
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.temp_mail = None
        self.email_provider: Optional[EmailProvider] = None
        self.state_file = "auth_state.json"
        # _owns_browser: a throw-away browser this process launched and must close.
        # _persistent: the detached browser with the saved profile; it is only
        # disconnected from, never closed, so it outlives the script.
        self._owns_browser = False
        self._persistent = False
        self.browser_process = None
        # True when sign-up is being resumed after an interrupted attempt, so a
        # Replit login found in the browser may well be the account being created.
        self.signup_resumed = False
        # True once the sign-up form was submitted in this process: from then on
        # a login in the browser may be the new account, never an old one.
        self.signup_submitted = False

    def log(self, message, color=Fore.WHITE):
        print(f"{color}{message}{Style.RESET_ALL}")

    def setup_browser(self):
        self.log("\n🌐 Setting up the PC browser...", Fore.CYAN)
        self.playwright = sync_playwright().start()

        # Prefer attaching to a browser the operator already opened. A normal
        # browser process cannot be attached to after launch; it must have been
        # started with a Chromium remote-debugging endpoint. When the endpoint
        # is not explicitly configured, probe common local CDP ports before
        # starting a managed browser.
        cdp_urls = self._candidate_cdp_urls()
        for cdp_url in cdp_urls:
            try:
                if self._attach_to_cdp(cdp_url):
                    return
            except Exception as exc:
                self.log(
                    f"Could not attach to browser at {cdp_url} ({exc}).",
                    Fore.YELLOW,
                )
                self.browser = None
                self.context = None

        # Nothing to reuse: open a browser window that outlives this script.
        if self._browser_mode() == "persistent" and self._start_persistent_browser():
            return

        # Load previous session state if it exists (skips login on future runs).
        # This is only used for a browser launched by this process.
        storage_state = self.state_file if os.path.exists(self.state_file) else None
        browser_channel = os.environ.get("PLAYWRIGHT_BROWSER_CHANNEL", "").strip()

        launch_errors = []
        if browser_channel:
            try:
                self.log(
                    f"Using configured Chromium browser channel: {browser_channel}.",
                    Fore.CYAN,
                )
                self._launch_managed_context(
                    channel=browser_channel,
                    storage_state=storage_state,
                )
            except Exception as exc:
                launch_errors.append(f"channel {browser_channel}: {exc}")
                self.log(
                    f"Configured browser channel/profile was unavailable ({exc}); "
                    "trying detected executables.",
                    Fore.YELLOW,
                )

        if self.context is None:
            for executable_path in self._browser_executable_candidates():
                try:
                    self.log(
                        f"Using detected browser executable: {executable_path}",
                        Fore.CYAN,
                    )
                    self._launch_managed_context(
                        executable_path=executable_path,
                        storage_state=storage_state,
                    )
                    break
                except Exception as exc:
                    launch_errors.append(f"{executable_path}: {exc}")

        if self.context is None:
            try:
                self.log(
                    "No configured or detected browser launched; "
                    "trying Playwright's bundled Chromium.",
                    Fore.CYAN,
                )
                self._launch_managed_context(
                    storage_state=storage_state,
                )
            except Exception as exc:
                launch_errors.append(f"Playwright bundled Chromium: {exc}")
                details = "; ".join(launch_errors)
                raise RuntimeError(
                    "No controllable or launchable Chromium-family browser was found. "
                    "Start a browser with remote debugging or install/configure "
                    "Chrome, Edge, Brave, or Chromium. "
                    f"Attempts: {details}"
                ) from exc

    @staticmethod
    def _browser_mode() -> str:
        mode = str(_setting("BROWSER_MODE", "persistent")).strip().lower()
        return mode if mode in {"persistent", "managed"} else "persistent"

    @staticmethod
    def _debug_port() -> int:
        try:
            return int(_setting("BROWSER_DEBUG_PORT", browser_locator.DEFAULT_DEBUG_PORT))
        except (TypeError, ValueError):
            return browser_locator.DEFAULT_DEBUG_PORT

    @staticmethod
    def _profile_dir() -> str:
        folder = str(_setting("BROWSER_PROFILE_DIR", browser_locator.DEFAULT_PROFILE_DIR))
        return os.path.abspath(os.path.expanduser(folder))

    @property
    def keeps_browser_open(self) -> bool:
        """True when the browser window stays open after this script ends."""

        return self._persistent or (self.browser is not None and not self._owns_browser)

    def _start_persistent_browser(self) -> bool:
        """Open the detached browser (own profile, stays open) and attach to it."""

        port = self._debug_port()
        profile = self._profile_dir()
        errors = []
        extra = shlex.split(str(_setting("BROWSER_EXTRA_ARGS", "")))
        for executable in self._browser_executable_candidates():
            try:
                self.log(
                    f"Opening a separate browser window with its own saved profile "
                    f"({os.path.basename(executable)}, profile {profile}).",
                    Fore.CYAN,
                )
                self.browser_process = browser_locator.launch_detached_browser(
                    executable, profile, port, extra_args=extra
                )
                if self._attach_to_cdp(f"http://127.0.0.1:{port}"):
                    self._persistent = True
                    self.log(
                        "This window stays open when the script ends, and keeps the "
                        "Replit login for the next run.",
                        Fore.GREEN,
                    )
                    return True
            except Exception as exc:
                errors.append(f"{executable}: {exc}")
                self.browser = None
                self.context = None
        if errors:
            self.log(
                "The persistent browser could not be started ("
                + "; ".join(errors)
                + "); falling back to a temporary window that closes with the script.",
                Fore.YELLOW,
            )
        return False

    def _launch_managed_context(
        self,
        *,
        executable_path: Optional[str] = None,
        channel: Optional[str] = None,
        storage_state: Optional[str] = None,
    ) -> None:
        """Launch an isolated managed browser and regular Playwright context."""

        launch_args = ["--no-sandbox"] if os.name != "nt" else None
        launch_kwargs = {
            "headless": False,
        }
        if launch_args is not None:
            launch_kwargs["args"] = launch_args
        if executable_path:
            launch_kwargs["executable_path"] = executable_path
        if channel:
            launch_kwargs["channel"] = channel

        browser = self.playwright.chromium.launch(**launch_kwargs)
        self.browser = browser
        self.context = browser.new_context(storage_state=storage_state)
        self._owns_browser = True

        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.set_default_timeout(30_000)

    def _candidate_cdp_urls(self) -> list[str]:
        """Return explicit and discoverable local Chromium CDP endpoints."""

        configured = os.environ.get("PLAYWRIGHT_CDP_URL", "").strip()
        if configured:
            return [configured]

        configured_ports = os.environ.get("PLAYWRIGHT_CDP_PORTS", "").strip()
        ports = []
        for raw_port in configured_ports.split(","):
            raw_port = raw_port.strip()
            if raw_port.isdigit():
                ports.append(int(raw_port))
        own_port = self._debug_port() if self._browser_mode() == "persistent" else None
        if own_port is not None and own_port not in ports:
            ports.insert(0, own_port)  # a window left open by the last run
        for port in (9222, 9223, 9224, 9225):
            if port not in ports:
                ports.append(port)

        discovered = []
        for port in ports:
            url = f"http://127.0.0.1:{port}"
            try:
                with urlopen(f"{url}/json/version", timeout=0.25) as response:
                    body = response.read(4096)
                if b"webSocketDebuggerUrl" in body:
                    discovered.append(url)
                    self.log(
                        f"Detected a browser remote-debugging endpoint at {url}.",
                        Fore.CYAN,
                    )
            except Exception:
                continue
        return discovered

    def _attach_to_cdp(self, cdp_url: str) -> bool:
        """Attach to one CDP endpoint and select its Replit or first tab."""

        self.browser = self.playwright.chromium.connect_over_cdp(cdp_url)
        contexts = self.browser.contexts
        if not contexts:
            raise RuntimeError("The attached browser has no browser context.")
        self.context = contexts[0]
        self._owns_browser = False
        self._persistent = (
            self._browser_mode() == "persistent"
            and cdp_url.rstrip("/").endswith(f":{self._debug_port()}")
        )
        self.page = self._find_page_for_host("replit.com")
        if self.page is None:
            self.page = self._first_open_page()
        if self.page is None:
            self.page = self.context.new_page()
        self.page.set_default_timeout(30_000)
        self.log(
            f"Attached to the existing browser at {cdp_url}; reusing its open "
            "tabs. Its window stays open when the script ends.",
            Fore.GREEN,
        )
        return True

    def _browser_executable_candidates(self) -> list[str]:
        """Find usable Chromium-family browser executables without assuming Edge."""

        return browser_locator.find_browser_executables()

    def _open_pages(self):
        """Return open pages from the attached/launched context."""

        if self.context is None:
            return []
        try:
            return [page for page in self.context.pages if not page.is_closed()]
        except Exception:
            return []

    @staticmethod
    def _page_host(page) -> str:
        try:
            return (urlsplit(page.url).hostname or "").casefold()
        except Exception:
            return ""

    def _find_page_for_host(self, host: str):
        host = host.casefold()
        for page in self._open_pages():
            page_host = self._page_host(page)
            if page_host == host or page_host.endswith(f".{host}"):
                return page
        return None

    def _first_open_page(self):
        pages = self._open_pages()
        return pages[0] if pages else None

    def _page_for_replit(self):
        """Select an existing Replit tab, creating only a tab if needed."""

        # Keep injected pages usable for offline tests and callers that provide
        # an already-selected Playwright page without the owning context.
        if self.context is None and self.page is not None:
            return self.page

        page = self._find_page_for_host("replit.com")
        if page is None:
            if self.context is None:
                raise RuntimeError("Browser context is not ready.")
            page = self.context.new_page()
        page.set_default_timeout(30_000)
        self.page = page
        # A tab that is not the active one is throttled by the browser (timers
        # and painting slow to a crawl), which makes every action on it slow.
        try:
            page.bring_to_front()
        except Exception:
            pass
        return page

    def attach_existing_only(self) -> bool:
        """Attach to a browser that is already running; never start one.

        Used to look at the real page before deciding what to do. Returns False
        (and leaves nothing running) when there is no browser to attach to.
        """

        if self.context is not None:
            return True
        self.playwright = sync_playwright().start()
        for cdp_url in self._candidate_cdp_urls():
            try:
                if self._attach_to_cdp(cdp_url):
                    return True
            except Exception:
                self.browser = None
                self.context = None
        try:
            self.playwright.stop()
        except Exception:
            pass
        self.playwright = None
        return False

    def observe_replit_page(self, page=None) -> Optional[str]:
        """What point of the Replit flow the Replit tab shows now, without navigating.

        Returns a ``pc_flow.classify_replit_page`` name, or None when there is
        no Replit tab to look at.
        """

        previous = self.page
        if page is None:
            page = self._find_page_for_host("replit.com")
            if page is None and self.context is None:
                page = self.page  # an injected page (offline tests)
            if page is None:
                return None
        self.page = page
        try:
            return classify_replit_page(
                page.url,
                self._page_text(),
                fields=self._signup_fields() is not None,
                chooser=self._visible_control(self.EMAIL_CHOICE_SELECTORS, timeout=300) is not None,
                challenge=self._visible_control(self.CAPTCHA_SELECTORS, timeout=300) is not None,
                create_button=self._visible_control(self.CREATE_ACCOUNT_SELECTORS, timeout=300)
                is not None,
            )
        except Exception:
            return None
        finally:
            if previous is not None:
                self.page = previous

    def _existing_session_choice(self) -> str:
        """Decide what to do about a Replit login that is already open at sign-up.

        Returns "delete", "signout", "keep", "manual" or "quit".
        EXISTING_SESSION_ACTION: "delete" (default) removes the old account from
        Replit's settings; "signout" only ends the login (own browser profile
        only); "keep" leaves it; "ask" asks every time. A resumed sign-up never
        deletes by itself, because the login may be the account being created.
        """

        mode = str(_setting("EXISTING_SESSION_ACTION", "delete")).strip().lower()
        if mode not in {"delete", "ask", "signout", "keep"}:
            mode = "ask"
        own_profile = self._owns_browser or self._persistent
        if mode == "keep":
            return "keep"
        if mode == "signout" and own_profile:
            return "signout"
        can_delete = not self.signup_resumed
        if mode == "delete" and can_delete:
            return "delete"
        self.log("\n⚠️ This browser is already logged in to a Replit account.", Fore.YELLOW)
        if self.signup_resumed:
            self.log(
                "   Sign-up is being resumed, so this may be the account that was just created.",
                Fore.YELLOW,
            )
        operator_alert.beep()
        options = "[k]eep it (it is the account being created)"
        if can_delete:
            options += " / [d]elete that account for good"
        options += " / [s]ign out here" + (" myself, then press ENTER" if not own_profile else "")
        answer = input(f"   {options} / [q]uit: ").strip().lower()
        if answer == "k":
            return "keep"
        if answer == "d" and can_delete:
            return "delete"
        if answer == "s":
            return "signout" if own_profile else "manual"
        return "quit"

    def _handle_existing_session(self):
        """Deal with an old Replit login before a new account is created.

        Returns None when sign-up should carry on (the browser is logged out
        now), True when the login that is open is kept as the account being
        created, False when the run should stop here.
        """

        choice = self._existing_session_choice()
        if choice == "keep":
            self.log("✅ Keeping the Replit login that is open.", Fore.GREEN)
            return True
        if choice == "quit":
            return False
        if choice == "manual":
            input("🖐️ Sign out of Replit in the browser, then press ENTER...")
        elif choice == "delete":
            verdict = self._delete_stale_account()
            if verdict == "own_account":
                self.log(
                    "✅ The account that is open is the one this run is creating; "
                    "keeping it.",
                    Fore.GREEN,
                )
                return True
            if verdict == "failed":
                self.log("❌ The old Replit account could not be removed.", Fore.RED)
                return False
        elif choice == "signout" and not self._sign_out_of_replit():
            self._save_failure("signup_signout_failed")
            self.log("❌ The old Replit login could not be ended.", Fore.RED)
            return False
        position = self.observe_replit_page()
        if position == "authenticated":
            self._save_failure("signup_still_logged_in")
            self.log("❌ The browser is still logged in to Replit.", Fore.RED)
            return False
        return None

    def _settle_replit_home(self) -> Optional[str]:
        """After opening replit.com, wait until the page shows what it is.

        A signed-in visitor is sent on to the workspace (``/~``) a moment after
        the page loads, so one look right after ``goto`` can miss it.
        """

        deadline = time.monotonic() + self.HOME_SETTLE_WAIT_MS / 1_000
        position = self.observe_replit_page()
        while time.monotonic() < deadline and (
            position in (None, "other")
            # An address of replit.com/ with nothing on it yet reads as "landing"
            # too; it only counts once the logged-out page really shows a button.
            or (position == "landing" and not self._shows_logged_out_home())
        ):
            self._pause(500)
            position = self.observe_replit_page()
        return position

    def _shows_logged_out_home(self) -> bool:
        try:
            return self._visible_control(self.CREATE_ACCOUNT_SELECTORS, timeout=300) is not None
        except Exception:
            return False

    def _wait_for_settings_page(self):
        """Settings > Profile as ``{"email", "username"}``; "signed_out" if Replit sent us to a login page; None on timeout."""

        deadline = time.monotonic() + self.SETTINGS_LOAD_WAIT_MS / 1_000
        while True:
            if is_profile_settings_text(self._page_text()):
                break
            if self.observe_replit_page() in {"login", "landing", "signup_form", "signup_chooser"}:
                return "signed_out"
            if time.monotonic() >= deadline:
                return None
            self._pause(self.POLL_MS)
        # The labels render before the e-mail line does.
        email_deadline = time.monotonic() + self.SETTINGS_EMAIL_WAIT_MS / 1_000
        identity = parse_account_identity(self._page_text())
        while not identity["email"] and time.monotonic() < email_deadline:
            self._pause(self.POLL_MS)
            identity = parse_account_identity(self._page_text())
        return identity

    def _delete_stale_account(self) -> str:
        """Delete the old Replit account this browser is signed in to.

        Path (attached_assets/step_2.1a-e): Settings > Profile, scroll to the
        bottom, "Request account deletion", "Yes, Delete my Account", then wait
        for Replit to land on the logged-out home page.

        Returns "deleted", "signed_out" (there was no login after all),
        "own_account" (it is the account this run is creating: nothing is
        touched) or "failed".

        Whatever account is signed in is deleted WITHOUT asking (there is no
        confirmation prompt). The one guard left, because deleting is
        permanent: the account whose e-mail is the address of THIS run is
        never deleted.
        """

        self.log("\n🧹 Replit opened already signed in (an old account). Opening its settings...", Fore.CYAN)
        try:
            self.page.goto(self.SETTINGS_PROFILE_URL, wait_until="domcontentloaded")
        except Exception as exc:
            self.log(f"ℹ️ Settings could not be opened ({type(exc).__name__}).", Fore.YELLOW)
            self._save_failure("stale_settings_not_opened")
            return "failed"
        identity = self._wait_for_settings_page()
        if identity == "signed_out":
            self.log("ℹ️ Replit showed a login page instead; there is no old account to delete.", Fore.YELLOW)
            return "signed_out"
        if identity is None:
            self._save_failure("stale_settings_not_loaded")
            self.log("❌ The settings page did not load.", Fore.RED)
            return "failed"

        email = identity.get("email")
        if email and self.email and email.strip().casefold() == self.email.strip().casefold():
            return "own_account"
        known = account_ledger.is_known(email)
        self.log(
            f"   Signed in as {email or 'an unreadable address'}"
            + (f" (@{identity['username']})" if identity.get("username") else "")
            + (" - an account this script created." if known else ".")
            + " Deleting it.",
            Fore.CYAN,
        )

        button = self._wait_for_control(self.REQUEST_DELETION_SELECTORS, timeout=self.DELETE_STEP_WAIT_MS)
        if button is None:
            self._save_failure("stale_delete_button_not_found")
            self.log("❌ 'Request account deletion' was not found on the settings page.", Fore.RED)
            return "failed"
        try:
            button.scroll_into_view_if_needed(timeout=3_000)
        except Exception:
            pass  # click() scrolls too
        self.log("[2/4] Clicking 'Request account deletion'...", Fore.CYAN)
        try:
            button.click(timeout=5_000)
        except Exception as exc:
            self._save_failure("stale_delete_button_click_failed")
            self.log(f"❌ The delete button could not be clicked ({type(exc).__name__}).", Fore.RED)
            return "failed"
        confirm = self._wait_for_control(self.CONFIRM_DELETION_SELECTORS, timeout=self.DELETE_STEP_WAIT_MS)
        if confirm is None:
            self._save_failure("stale_delete_dialog_not_found")
            self.log("❌ The confirmation dialog did not appear.", Fore.RED)
            return "failed"
        self.log("[3/4] Confirming 'Yes, Delete my Account'...", Fore.CYAN)
        try:
            confirm.click(timeout=5_000)
        except Exception as exc:
            self._save_failure("stale_delete_confirm_failed")
            self.log(f"❌ The confirmation could not be clicked ({type(exc).__name__}).", Fore.RED)
            return "failed"

        self.log(
            "[4/4] Deletion requested. Waiting for Replit to return to the logged-out "
            "home page (this can take a while)...",
            Fore.CYAN,
        )
        if self._wait_for_logged_out_home():
            self.log("✅ The old account is gone and Replit is logged out.", Fore.GREEN)
            return "deleted"
        # The request was accepted but the login lingers. In the run's own
        # profile the session cookies can simply be cleared.
        if (self._owns_browser or self._persistent) and self._sign_out_of_replit():
            self.log("✅ Deletion requested; the lingering login was cleared.", Fore.GREEN)
            return "deleted"
        self._save_failure("stale_delete_not_logged_out")
        return "failed"

    def _wait_for_logged_out_home(self) -> bool:
        """After the deletion was confirmed: wait until Replit shows the logged-out pages."""

        deadline = time.monotonic() + self.LOGGED_OUT_WAIT_MS / 1_000
        reloaded_at = time.monotonic() + self.LOGGED_OUT_WAIT_MS / 2_000
        reloaded = False
        while True:
            if self.observe_replit_page() in {"landing", "login", "signup_chooser", "signup_form"}:
                return True
            now = time.monotonic()
            if not reloaded and now >= reloaded_at:
                reloaded = True  # one fresh look at the home page
                try:
                    self.page.goto(self.REPLIT_URL, wait_until="domcontentloaded")
                except Exception:
                    pass
            if now >= deadline:
                return False
            self._pause(500)

    def _sign_out_of_replit(self) -> bool:
        """End the Replit login in this browser profile (cookies of replit.com only)."""

        try:
            self.context.clear_cookies(domain=re.compile(r"(^|\.)replit\.com$"))
        except Exception as exc:
            self.log(f"ℹ️ Could not clear the Replit cookies ({exc}).", Fore.YELLOW)
            return False
        self.page.goto(self.REPLIT_URL, wait_until="domcontentloaded")
        try:
            self.page.wait_for_load_state("load", timeout=5_000)
        except PlaywrightTimeoutError:
            pass
        return self.observe_replit_page() != "authenticated"

    def obtain_temp_email(self) -> str:
        """Obtain the configured address through the provider abstraction."""

        provider = self._ensure_email_provider()
        self.email = provider.obtain_address()
        return self.email

    def _ensure_temp_mail(self) -> TempMailOrgProvider:
        if self.context is None:
            self.setup_browser()
        if self.temp_mail is None:
            self.temp_mail = TempMailOrgProvider(
                self.context,
                url=self.temp_mail_url,
            )
        return self.temp_mail

    def _ensure_email_provider(self) -> EmailProvider:
        if self.email_provider is not None:
            return self.email_provider
        if self.context is None:
            self.setup_browser()
        self.email_provider = create_email_provider(
            context=self.context,
            strategy=self.email_strategy,
            primary_api=self.primary_email_api,
            custom_email=self.user_custom_email,
            timeout_seconds=self.email_check_timeout,
            temp_mail_url=self.temp_mail_url,
        )
        if self.email:
            self.email_provider.address = self.email
            # A resumed hybrid run must continue with the provider that
            # created the saved address rather than generating a new mailbox.
            if hasattr(self.email_provider, "primary"):
                self.email_provider.primary.address = self.email
                self.email_provider.active = self.email_provider.primary
        if isinstance(self.email_provider, TempMailOrgProvider):
            self.temp_mail = self.email_provider
        return self.email_provider

    def discard_mailbox(self) -> bool:
        """Delete the temporary mailbox (its address is no longer needed)."""

        try:
            return bool(self._ensure_email_provider().discard_mailbox())
        except Exception as exc:
            self.log(f"ℹ️ The mailbox could not be deleted ({exc}).", Fore.YELLOW)
            return False

    def open_verification_message(self):
        """Open the Replit verification message through the active provider."""

        return self._ensure_email_provider().open_verification_message()

    def verify_email(self):
        """Complete verification and prove success through the active provider."""

        return self._ensure_email_provider().verify_email()

    @property
    def verification_url(self) -> Optional[str]:
        provider = self.email_provider
        return getattr(provider, "verification_url", None) if provider else None

    @property
    def email_provider_name(self) -> Optional[str]:
        provider = self.email_provider
        return getattr(provider, "provider_name", None) if provider else None

    def save_state(self):
        """Save cookies and session state for future runs"""
        try:
            self.context.storage_state(path=self.state_file)
            self.log("💾 Session state saved.", Fore.GREEN)
        except Exception:
            pass

    def _visible_control(self, selectors, timeout=1_500, require_enabled=False):
        for selector in selectors:
            locator = self.page.locator(selector)
            try:
                for index in range(locator.count()):
                    candidate = locator.nth(index)
                    if candidate.is_visible(timeout=timeout):
                        if require_enabled and hasattr(candidate, "is_enabled"):
                            if not candidate.is_enabled(timeout=timeout):
                                continue
                        return candidate
            except Exception:
                continue
        return None

    def _icon_only_dialog_button(self):
        """Return the unlabeled (×) button of a visible dialog, never a labeled one.

        The Google one-tap pop-up's close control may carry no aria-label. Matching
        any dialog button could press "Continue with Google" and start an OAuth
        flow, so only buttons without visible text are considered.
        """

        locator = self.page.locator("[role='dialog'] button")
        try:
            for index in range(locator.count()):
                candidate = locator.nth(index)
                if not candidate.is_visible(timeout=500):
                    continue
                label = " ".join(candidate.inner_text().split())
                if label in {"", "×", "x", "X"}:
                    return candidate
        except Exception:
            return None
        return None

    def _wait_for_control(self, selectors, timeout=10_000):
        """Wait for a visible, enabled control without selecting a backdrop control."""

        deadline = time.monotonic() + (timeout / 1_000)
        while True:
            control = self._visible_control(
                selectors,
                timeout=min(500, max(1, int((deadline - time.monotonic()) * 1_000))),
                require_enabled=True,
            )
            if control is not None:
                return control
            if time.monotonic() >= deadline:
                return None
            self.page.wait_for_timeout(100)

    def _page_text(self) -> str:
        try:
            return self.page.locator("body").inner_text()
        except Exception:
            return ""

    def _signup_scope_text(self) -> str:
        """Return the visible dialog/form text, keeping its line structure.

        Replit keeps the landing page mounted behind the sign-up modal, so the
        modal's own text is read first; unrelated page text must not block (or
        satisfy) a registration attempt. Lines are kept apart because the
        validation patterns are applied line by line.
        """

        for scope_selector in ("[role='dialog']", "dialog", "form"):
            scope = self.page.locator(scope_selector)
            try:
                for index in range(scope.count()):
                    candidate = scope.nth(index)
                    if candidate.is_visible(timeout=500):
                        text = candidate.inner_text()
                        if " ".join(text.split()):
                            return text
            except Exception:
                continue
        return ""

    def _validation_errors(self, scoped_text: Optional[str] = None) -> list[str]:
        if scoped_text is None:
            scoped_text = self._signup_scope_text()

        errors = find_validation_errors(scoped_text or self._page_text())
        for selector in self.VALIDATION_SELECTORS:
            locator = self.page.locator(selector)
            try:
                for index in range(locator.count()):
                    candidate = locator.nth(index)
                    if candidate.is_visible(timeout=500):
                        text = " ".join(candidate.inner_text().split())
                        # Class names and aria-invalid markers are signals, not
                        # error messages. Only retain text matching the known
                        # validation patterns; this avoids treating helper
                        # text such as "Password is valid" as a failure.
                        for error in find_validation_errors(text):
                            if error not in errors:
                                errors.append(error)
            except Exception:
                continue
        return errors

    def _captcha_present(self) -> bool:
        if has_security_challenge_text(self._page_text()):
            return True
        return self._visible_control(self.CAPTCHA_SELECTORS, timeout=500) is not None

    def _security_challenge_present(self) -> bool:
        """Detect CAPTCHA and other challenges that require manual takeover."""

        return self._captcha_present()

    def _wait_for_session_or_login(self, timeout=30_000) -> str:
        """Wait for an observable authenticated or login-required state."""

        try:
            self.page.wait_for_function(
                """() => {
                    const text = (document.body?.innerText || "").toLowerCase();
                    const path = window.location.pathname.toLowerCase();
                    return text.includes("personal workspace") ||
                           text.includes("what are we working on today") ||
                           text.includes("my repls") ||
                           path.endsWith("/login") ||
                           Boolean(document.querySelector("input[type='password']"));
                }""",
                timeout=timeout,
            )
        except PlaywrightTimeoutError:
            return "unknown"
        state = classify_session_state(self.page.url, self._page_text())
        if state == "authenticated":
            return state
        try:
            if self.page.locator("input[type='password']").is_visible(timeout=500):
                return "login_required"
        except Exception:
            pass
        return state

    def _wait_for_authenticated_state(self, timeout=30_000) -> bool:
        """Wait for and require an observable authenticated state."""

        try:
            self.page.wait_for_function(
                """() => {
                    const text = (document.body?.innerText || "").toLowerCase();
                    return text.includes("personal workspace") ||
                           text.includes("what are we working on today") ||
                           text.includes("my repls");
                }""",
                timeout=timeout,
            )
        except PlaywrightTimeoutError:
            return False
        return classify_session_state(self.page.url, self._page_text()) == "authenticated"

    def wait_for_session_sync(self, timeout=30_000, poll_interval=1_000) -> str:
        """Reload until the post-Android browser state is observable.

        Android completion can be ahead of the browser's current document. Each
        probe performs a real reload and classifies the resulting page. Only an
        authenticated or login-required state ends the wait; an unknown page
        remains a failure and never advances the stage.
        """

        if self.page is None:
            return "unknown"

        deadline = time.monotonic() + (timeout / 1_000)
        attempted = False
        last_state = "unknown"

        while not attempted or time.monotonic() < deadline:
            attempted = True
            remaining_ms = max(
                1,
                int((deadline - time.monotonic()) * 1_000),
            )
            try:
                self.page.reload(
                    wait_until="domcontentloaded",
                    timeout=remaining_ms,
                )
                try:
                    self.page.wait_for_load_state(
                        "networkidle",
                        timeout=min(remaining_ms, 5_000),
                    )
                except PlaywrightTimeoutError:
                    pass
                last_state = classify_session_state(
                    self.page.url,
                    self._page_text(),
                )
                if last_state in {"authenticated", "login_required"}:
                    return last_state
            except PlaywrightTimeoutError:
                last_state = "unknown"
            except Exception:
                self._save_failure("session_sync_probe_failed")
                return "unknown"

            if time.monotonic() >= deadline:
                break
            self.page.wait_for_timeout(
                min(poll_interval, max(1, int((deadline - time.monotonic()) * 1_000)))
            )

        self._save_failure("session_sync_timeout")
        return "unknown"

    def _save_failure(self, tag: str) -> None:
        try:
            os.makedirs("screenshots", exist_ok=True)
            self.page.screenshot(path=os.path.join("screenshots", f"pc_{tag}.png"))
        except Exception:
            pass

    def _pause(self, milliseconds: int) -> None:
        """Wait inside the page so the browser keeps processing events."""

        try:
            self.page.wait_for_timeout(milliseconds)
        except Exception:
            time.sleep(milliseconds / 1_000)

    def _wait_for_challenge_clear(self) -> bool:
        """Give the page a moment to move on after the operator solved a challenge."""

        deadline = time.monotonic() + self.CHALLENGE_CLEAR_WAIT_MS / 1_000
        while True:
            if not self._security_challenge_present():
                return True
            if time.monotonic() >= deadline:
                return False
            self._pause(self.POLL_MS)

    def handle_captcha(self):
        """Pause for manual security handling and verify it is resolved.

        A person completes the challenge; nothing here solves or retries it.
        The run makes a sound and brings the window forward, then carries on by
        itself once the challenge has been gone for a moment. ENTER in the
        terminal still works as "I am done, check now".
        """

        if not self._security_challenge_present():
            return True
        self.log("\n⚠️ Security or anti-bot challenge detected.", Fore.YELLOW)
        self.log(
            "🛑 PAUSING: Complete it manually in the browser window; "
            "automation will not solve or retry it.",
            Fore.YELLOW,
        )
        self.log(
            "   The run carries on by itself once the challenge is gone, or "
            "press ENTER here to say you are done.",
            Fore.YELLOW,
        )
        self.log(
            "   If the form is still on screen afterwards, press its submit "
            "button yourself.",
            Fore.YELLOW,
        )
        self._call_attention(bring_forward=True)
        if self._wait_for_operator() == "gone":
            self.log("✅ Security challenge no longer visible. Resuming.", Fore.GREEN)
            return True
        # The person said they are done (ENTER): confirm before carrying on.
        if not self._wait_for_challenge_clear():
            self._save_failure("captcha_unresolved")
            self.log("❌ Security challenge still appears unresolved.", Fore.RED)
            return False
        self.log("✅ Security challenge no longer visible. Resuming.", Fore.GREEN)
        return True

    def _call_attention(self, bring_forward: bool = False) -> None:
        """Sound, and optionally the browser window to the front, so a person notices."""

        try:
            operator_alert.beep()
        except Exception:
            pass
        if bring_forward:
            try:
                self.page.bring_to_front()
            except Exception:
                pass

    def _wait_for_operator(self) -> str:
        """Wait while a person completes a challenge.

        Returns "gone" once the challenge has stayed away for CHALLENGE_SETTLE_MS
        (a challenge that flickers between rounds does not count), or "enter" when
        the person pressed ENTER or the long wait ended and they were asked.
        """

        operator_alert.discard_pending_input()  # an old ENTER must not count
        started = time.monotonic()
        next_reminder = started + self.CHALLENGE_REMIND_S
        gone_since = None
        while True:
            now = time.monotonic()
            if self._security_challenge_present():
                gone_since = None
            else:
                if gone_since is None:
                    gone_since = now
                if now - gone_since >= self.CHALLENGE_SETTLE_MS / 1_000:
                    return "gone"
            if operator_alert.enter_pressed():
                return "enter"
            if now - started >= self.CHALLENGE_AUTO_WAIT_S:
                self._call_attention()
                input("👉 Still waiting. Press ENTER here once the challenge is solved...")
                return "enter"
            if now >= next_reminder:
                next_reminder = now + self.CHALLENGE_REMIND_S
                self.log(
                    "⏳ Still waiting for the challenge to be completed in the "
                    "browser window...",
                    Fore.YELLOW,
                )
                self._call_attention()
            self._pause(self.CHALLENGE_POLL_MS)

    def _tap(self, control, label: str, taps: dict) -> bool:
        """Click one control; a failed click is logged and counted, never raised."""

        taps[label] = taps.get(label, 0) + 1
        try:
            control.click(timeout=3_000)
        except Exception as exc:
            self.log(
                f"ℹ️ '{label}' could not be clicked yet ({type(exc).__name__}); "
                "re-checking the page.",
                Fore.YELLOW,
            )
            return False
        return True

    def _dialog_visible(self) -> bool:
        return self._visible_control(("[role='dialog']", "dialog[open]"), timeout=300) is not None

    def _signup_fields(self):
        """Return the visible (email, password) inputs of the sign-up form, else None."""

        password_field = self._visible_control(self.PASSWORD_FIELD_SELECTORS, timeout=300)
        if password_field is None:
            return None
        email_field = self._visible_control(self.EMAIL_FIELD_SELECTORS, timeout=300)
        if email_field is None:
            return None
        return email_field, password_field

    def _open_signup_form(self):
        """Reach the credentials form from wherever the page currently is.

        The landing page renders after the document loads, the chooser modal
        after a click and the form after another one, so every step is an
        observe-then-act loop with real waits instead of one instant check.
        A challenge anywhere on the way goes to the manual pause.
        """

        now = time.monotonic()
        deadline = now + self.SIGNUP_FORM_WAIT_MS / 1_000
        last_progress = now
        taps: dict = {}
        deep_linked = False
        manual_pauses = 0
        entry_steps = (
            (
                "continue with email",
                self.EMAIL_CHOICE_SELECTORS,
                "[4] Selecting 'Continue with Email'...",
            ),
            (
                "create account",
                self.CREATE_ACCOUNT_SELECTORS,
                "[3] Clicking 'Create account'...",
            ),
        )
        while True:
            fields = self._signup_fields()
            if fields is not None:
                return fields
            if is_workspace_url(self.page.url):
                return None  # signed in: there is no sign-up form here (the caller deals with it)

            if self._security_challenge_present():
                manual_pauses += 1
                if manual_pauses > self.MAX_MANUAL_CHALLENGES or not self.handle_captcha():
                    return None
                last_progress = time.monotonic()
                continue

            acted = False
            for label, selectors, announcement in entry_steps:
                if taps.get(label, 0) >= self.CONTROL_TAP_LIMIT:
                    continue
                # The header button sits behind an open modal; never press it
                # (or, worse, the modal's own submit button) from here.
                if label == "create account" and self._dialog_visible():
                    continue
                control = self._visible_control(selectors, timeout=300)
                if control is None:
                    continue
                self.log(announcement, Fore.CYAN)
                if self._tap(control, label, taps):
                    last_progress = time.monotonic()
                acted = True
                break
            if acted:
                self._pause(400)  # let the modal swap its content
                continue

            now = time.monotonic()
            if (
                not deep_linked
                and now - last_progress > self.SIGNUP_ENTRY_PATIENCE_MS / 1_000
            ):
                deep_linked = True
                taps.clear()
                self.log(
                    "ℹ️ No sign-up control appeared; opening the sign-up link directly.",
                    Fore.YELLOW,
                )
                self.page.goto(self.SIGNUP_DEEP_LINK, wait_until="domcontentloaded")
                last_progress = time.monotonic()
                continue
            if now >= deadline:
                return None
            self._pause(self.POLL_MS)

    @staticmethod
    def _fill_field(field, value: str) -> bool:
        """Fill an input and confirm the page kept the value."""

        for _attempt in range(2):
            field.fill(value)
            try:
                if field.input_value() == value:
                    return True
            except Exception:
                return True  # the value cannot be read back; trust fill()
        return False

    def _wait_for_signup_form_validation(self) -> str:
        """Wait for Replit's asynchronous field checks: valid, invalid or pending.

        Both fields show a spinner while Replit checks them and then
        "Email is available" / "Password is valid" (attached_assets/3.1-3.3).
        The submit button already looks enabled during the check, so clicking
        immediately races it.
        """

        deadline = time.monotonic() + self.SIGNUP_VALIDATION_WAIT_MS / 1_000
        while True:
            text = self._signup_scope_text() or self._page_text()
            verdict = classify_signup_form_text(text)
            if verdict == "pending" and self._validation_errors(text):
                verdict = "invalid"  # error markers (role=alert, aria-invalid)
            if verdict != "pending":
                return verdict
            if time.monotonic() >= deadline:
                return "pending"
            self._pause(self.POLL_MS)

    def _wait_for_signup_result(self, baseline_url: str) -> str:
        """Poll until the page shows a sign-up result.

        Returns "submitted", "validation", "challenge" (unresolved after the
        manual pause) or "timeout". A challenge the operator solved does not
        end the wait: the page state that follows is still observed.
        """

        deadline = time.monotonic() + self.SIGNUP_RESULT_WAIT_MS / 1_000
        manual_pauses = 0
        while True:
            state = classify_signup_state(
                self.page.url,
                self._page_text(),
                baseline_url=baseline_url,
            )
            if (
                state == "waiting"
                and self._visible_control(self.CAPTCHA_SELECTORS, timeout=300) is not None
            ):
                state = "captcha"  # challenge frames carry no page text
            if state in {"captcha", "security_challenge"}:
                manual_pauses += 1
                if manual_pauses > self.MAX_MANUAL_CHALLENGES:
                    self._save_failure("signup_challenge_repeated")
                    self.log(
                        "❌ The security challenge keeps coming back; giving up "
                        "on this attempt instead of pausing forever.",
                        Fore.RED,
                    )
                    return "challenge"
                if not self.handle_captcha():
                    return "challenge"
                deadline = time.monotonic() + self.SIGNUP_RESULT_WAIT_MS / 1_000
                continue
            if state in {"submitted", "validation"}:
                return state
            if time.monotonic() >= deadline:
                return "timeout"
            self._pause(self.POLL_MS)

    def create_account(self, email: Optional[str] = None, password: Optional[str] = None):
        self.log("\n" + "="*60, Fore.MAGENTA)
        self.log("💻 PC: Creating Replit Account", Fore.MAGENTA)
        self.log("="*60, Fore.MAGENTA)

        try:
            if email is not None:
                self.email = email
            if password is not None:
                self.password = password
            self._page_for_replit()
            # Look before acting: the page may already be part-way through
            # sign-up (the person did a step by hand, or a run was interrupted).
            position = self.observe_replit_page()
            if position is not None:
                self.log(f"🔎 Sign-up position detected: {position}.", Fore.CYAN)
            if position == "check_email":
                self.log("✅ Replit already shows the 'check your email' page.", Fore.GREEN)
                return True
            if position == "authenticated":
                verdict = self._handle_existing_session()
                if verdict is not None:
                    return verdict
                position = self.observe_replit_page()
            in_place = position in {"signup_form", "signup_chooser", "challenge"}

            if in_place:
                self.log("[1] Continuing on the sign-up page that is already open...", Fore.CYAN)
            else:
                self.log("\n[1] Navigating to Replit...", Fore.CYAN)
                self.page.goto(self.REPLIT_URL, wait_until="domcontentloaded")
                try:
                    self.page.wait_for_load_state("load", timeout=5_000)
                except PlaywrightTimeoutError:
                    pass
                # Replit sends a signed-in visitor on to /~ a moment later. Look
                # again instead of searching a workspace for a "Create account"
                # button and then jumping to the sign-up link.
                if self._settle_replit_home() == "authenticated":
                    verdict = self._handle_existing_session()
                    if verdict is not None:
                        return verdict

            self.log("[2] Checking for Google One-Tap popup...", Fore.CYAN)
            close_btn = None if in_place else self._visible_control(
                (
                    "button[aria-label='Close']",
                    "[role='dialog'] button[aria-label='Close']",
                ),
                timeout=1_500,
            ) or self._icon_only_dialog_button()
            if in_place:
                self.log("ℹ️ Skipped (the sign-up dialog is open).", Fore.YELLOW)
            elif close_btn is not None:
                try:
                    close_btn.click(timeout=3_000)
                    self.log("✅ Closed Google popup", Fore.GREEN)
                except Exception as exc:
                    # The pop-up can vanish between the visibility check and the
                    # click; it is optional, so sign-up carries on without it.
                    self.log(
                        f"ℹ️ The Google popup could not be closed "
                        f"({type(exc).__name__}); continuing...",
                        Fore.YELLOW,
                    )
            else:
                self.log("ℹ️ No popup found, continuing...", Fore.YELLOW)

            fields = self._open_signup_form()
            if fields is None and is_workspace_url(self.page.url):
                # The redirect to the signed-in workspace came late.
                verdict = self._handle_existing_session()
                if verdict is not None:
                    return verdict
                fields = self._open_signup_form()
            if fields is None:
                self._save_failure("signup_form_not_found")
                self.log(
                    "❌ The email/password form did not appear "
                    "(no sign-up control, or a challenge was not resolved).",
                    Fore.RED,
                )
                return False
            email_field, password_field = fields

            self.log("[5] Entering credentials...", Fore.CYAN)
            if not (
                self._fill_field(email_field, self.email or "")
                and self._fill_field(password_field, self.password)
            ):
                self._save_failure("signup_fill_failed")
                self.log("❌ The form did not keep the typed credentials.", Fore.RED)
                return False
            self.log(f"✅ Email: {self.email}", Fore.GREEN)
            self.log("✅ Password entered.", Fore.GREEN)

            self.log("   Waiting for Replit to validate the fields...", Fore.CYAN)
            verdict = self._wait_for_signup_form_validation()
            if verdict == "invalid":
                validation_errors = self._validation_errors()
                self._save_failure("signup_validation_before_submit")
                self.log(
                    f"❌ Registration validation blocked submission "
                    f"({len(validation_errors)} visible error(s)).",
                    Fore.RED,
                )
                return False
            if verdict == "pending":
                self.log(
                    "ℹ️ The validation messages were not seen; submitting anyway "
                    "and relying on the observed result.",
                    Fore.YELLOW,
                )

            # Handle CAPTCHA before submitting
            if not self.handle_captcha():
                return False

            self.log("[6] Submitting form...", Fore.CYAN)
            submit_btn = self._wait_for_control(self.SUBMIT_SELECTORS, timeout=10_000)
            if submit_btn is None:
                self._save_failure("signup_submit_not_found")
                self.log("❌ Registration submit control was not found.", Fore.RED)
                return False
            # The modal has a second, visually similar header button. Use the
            # form submit control above and verify that the click is accepted.
            submit_url = self.page.url
            self.signup_submitted = True
            submit_btn.click()
            self.log(
                f"⏳ Waiting up to {self.SIGNUP_RESULT_WAIT_MS // 1_000} seconds "
                "for Replit's response (a challenge pauses for manual handling)...",
                Fore.CYAN,
            )

            result = self._wait_for_signup_result(submit_url)
            if result == "challenge":
                return False
            if result == "validation":
                self._save_failure("signup_validation_after_submit")
                self.log("❌ Registration returned a visible validation error.", Fore.RED)
                return False
            if result != "submitted":
                self._save_failure("signup_result_timeout")
                self.log("❌ No registration result state was observed.", Fore.RED)
                return False

            self.log("\n✅ Account creation entered an observed result state.", Fore.GREEN)
            return True

        except Exception as e:
            self._save_failure("signup_exception")
            self.log(f"\n❌ Error creating account: {e}", Fore.RED)
            return False

    def login_after_mobile(self):
        self.log("\n" + "="*60, Fore.MAGENTA)
        self.log("💻 PC: Logging in after mobile onboarding", Fore.MAGENTA)
        self.log("="*60, Fore.MAGENTA)
        
        try:
            self.log("\n[1] Navigating to Replit home...", Fore.CYAN)
            self._page_for_replit()
            self.page.goto("https://replit.com", wait_until="domcontentloaded")
            initial_state = self._wait_for_session_or_login()
            if initial_state == "authenticated":
                self.log("\n✅ Already logged in! Main interface loaded.", Fore.GREEN)
                self.save_state()
                return True
            
            self.log("[2] Logging in manually...", Fore.CYAN)
            self.page.goto("https://replit.com/login", wait_until="domcontentloaded")
            email_field = self.page.locator(
                'input[type="text"], input[type="email"], input[name="email"]'
            ).first
            password_field = self.page.locator(
                'input[type="password"], input[name="password"]'
            ).first
            email_field.wait_for(state="visible", timeout=15_000)
            password_field.wait_for(state="visible", timeout=15_000)
            email_field.fill(self.email or "")
            password_field.fill(self.password)
            
            if not self.handle_captcha():
                return False
            
            login_button = self._visible_control(
                ('button:has-text("Log In")', 'button[type="submit"]'),
                timeout=10_000,
            )
            if login_button is None:
                self._save_failure("login_submit_not_found")
                self.log("\n❌ Login control was not found.", Fore.RED)
                return False
            login_button.click()

            if self._captcha_present():
                if not self.handle_captcha():
                    return False
            if not self._wait_for_authenticated_state():
                self._save_failure("login_success_not_observed")
                self.log("\n❌ Login completed without an observed authenticated state.", Fore.RED)
                return False

            self.log("\n✅ Login completed with an observed authenticated state!", Fore.GREEN)
            self.save_state()
            return True
            
        except Exception as e:
            self.log(f"\n❌ Error logging in: {e}", Fore.RED)
            return False

    def _on_import_page(self, page) -> bool:
        """True while the tab shows replit.com/import/... (a signed-out visitor is sent to /login)."""

        try:
            parts = urlsplit(page.url)
        except Exception:
            return True  # cannot tell: do not give up on it
        host = (parts.hostname or "").casefold()
        return host in ("replit.com", "www.replit.com") and parts.path.startswith("/import")

    def _github_url_box(self, page):
        """The visible "GitHub Repo URL" input of the import page, or None."""

        finders = [
            # placeholder: https://github.com/username/repo
            lambda: page.locator('input[placeholder*="github.com" i]').first,
            lambda: page.locator('input[aria-label*="GitHub Repo" i]').first,
            lambda: page.get_by_label("GitHub Repo URL").first,
        ]
        for find in finders:
            try:
                box = find()
                if box.is_visible():
                    return box
            except Exception:
                continue
        return None

    def open_github_import_page(self) -> bool:
        """Take the Replit tab to replit.com/import/github. Never raises.

        True when the tab is on that page. False when it could not be opened or
        Replit sent it elsewhere (usually the login page, because nobody is
        signed in): the reason is logged and the tab is left where it is. A
        signed-out visitor is redirected a moment after the page loads, so the
        tab is watched for a few seconds, ending as soon as the page has
        rendered its box or has been sent away.
        """

        try:
            page = self._page_for_replit()
            self.log(f"\n🌐 Opening {self.GITHUB_IMPORT_URL}", Fore.CYAN)
            page.goto(self.GITHUB_IMPORT_URL, wait_until="domcontentloaded")
            waited = 0
            while True:
                if not self._on_import_page(page):
                    self.log(
                        f"⚠️ Replit sent the browser to {page.url} instead of the import page "
                        "(is the account still signed in?).",
                        Fore.YELLOW,
                    )
                    return False
                if self._github_url_box(page) is not None or waited >= self.GITHUB_IMPORT_SETTLE_MS:
                    break  # rendered, or still loading: enter_github_repo_url keeps watching
                page.wait_for_timeout(self.GITHUB_IMPORT_POLL_MS)
                waited += self.GITHUB_IMPORT_POLL_MS
        except Exception as exc:
            self.log(f"⚠️ Could not open the GitHub import page ({exc}).", Fore.YELLOW)
            return False
        self.log("✅ The GitHub import page is open.", Fore.GREEN)
        return True

    def enter_github_repo_url(self, repo_url: str) -> bool:
        """Type ``repo_url`` into the import page's "GitHub Repo URL" box. Never raises.

        Only that box is filled. Nothing is clicked, so starting the import (and
        connecting a GitHub account, which a private repository needs) stays
        with the person at the keyboard. When the box cannot be filled the link
        is printed so it can be pasted by hand.
        """

        try:
            page = self._page_for_replit()
            box = None
            waited = 0
            while waited <= self.GITHUB_IMPORT_LOAD_WAIT_MS:
                if not self._on_import_page(page):
                    self.log(
                        f"⚠️ The browser is on {page.url}, not on the import page "
                        "(is the account still signed in?).",
                        Fore.YELLOW,
                    )
                    break
                box = self._github_url_box(page)
                if box is not None:
                    break
                page.wait_for_timeout(self.GITHUB_IMPORT_POLL_MS)
                waited += self.GITHUB_IMPORT_POLL_MS
            if box is None:
                self._save_failure("github_import_box_not_found")
                self.log('⚠️ Could not find the "GitHub Repo URL" box. Paste the link yourself:', Fore.YELLOW)
                self.log(f"   {repo_url}", Fore.YELLOW)
                return False
            if not self._fill_field(box, repo_url):
                self.log("⚠️ The page did not keep the link. Paste it yourself:", Fore.YELLOW)
                self.log(f"   {repo_url}", Fore.YELLOW)
                return False
        except Exception as exc:
            self.log(f"⚠️ Could not type the link into the import page ({exc}).", Fore.YELLOW)
            self.log(f"   Paste it yourself: {repo_url}", Fore.YELLOW)
            return False
        self.log(f"✅ GitHub link typed into the import page: {repo_url}", Fore.GREEN)
        self.log("   Finish the import in the browser; nothing was clicked for you.", Fore.CYAN)
        return True

    def import_github_repo(self, repo_url):
        self.log("\n" + "="*60, Fore.MAGENTA)
        self.log("📥 PC: Importing GitHub Repository", Fore.MAGENTA)
        self.log("="*60, Fore.MAGENTA)
        
        try:
            self.log(f"\n[1] Importing: {repo_url}", Fore.CYAN)
            self._page_for_replit()
            
            # Method 1: Try to find the AI chat input (Replit Agent / Create Repl)
            chat_input = self.page.locator('textarea[placeholder*="Start chatting"], textarea[placeholder*="describe a task"], input[placeholder*="Make an app"]').first
            
            if chat_input.is_visible(timeout=5000):
                self.log("[2] Using AI prompt to import...", Fore.CYAN)
                prompt = f"Import this GitHub repository: {repo_url}"
                chat_input.fill(prompt)
                chat_input.press('Enter')
                
                self.log("\n✅ Import prompt submitted!", Fore.GREEN)
                self.log("⏳ Waiting for Replit to expose the imported project...", Fore.CYAN)
                try:
                    self.page.wait_for_function(
                        """() => {
                            const text = document.body?.innerText || "";
                            return window.location.href.includes("/~/") ||
                                   /imported|creating|ready|project/i.test(text);
                        }""",
                        timeout=30_000,
                    )
                except PlaywrightTimeoutError:
                    self._save_failure("github_import_result_timeout")
                    self.log(
                        "\n❌ Import was submitted, but no observable progress or "
                        "project state appeared.",
                        Fore.RED,
                    )
                    return False

                current_url = self.page.url
                if "/~/" in current_url:
                    self.log("\n✅ Project imported successfully!", Fore.GREEN)
                    self.log(f"📍 URL: {current_url}", Fore.GREEN)
                    return True

                self._save_failure("github_import_success_not_observed")
                self.log(
                    "\n❌ Import progress was visible, but a project URL was not "
                    "observed.",
                    Fore.RED,
                )
                return False
            else:
                self._save_failure("github_import_control_not_found")
                self.log("\n⚠️ Could not find AI chat input. Please import manually.", Fore.RED)
                return False
            
        except Exception as e:
            self.log(f"\n❌ Error importing repo: {e}", Fore.RED)
            return False

    def close_browser(self):
        if self.keeps_browser_open:
            self.log("\n🔌 Disconnecting from the browser (its window stays open)...", Fore.CYAN)
        else:
            self.log("\n🛑 Closing browser...", Fore.CYAN)
        # storage_state() exports every cookie in the context. A context this
        # process launched, or the dedicated persistent profile, only holds
        # Replit and mailbox sessions, but a browser the operator attached us to
        # is their own profile, so its cookies are never exported.
        if self.context and (self._owns_browser or self._persistent):
            self.save_state()
        if self._persistent and not self._owns_browser:
            self.log(
                "ℹ️ Leaving the browser window open; its saved profile keeps the "
                "Replit login.",
                Fore.CYAN,
            )
        if self.browser and self._owns_browser:
            try:
                is_connected = getattr(self.browser, "is_connected", None)
                if callable(is_connected) and not is_connected():
                    self.log(
                        "ℹ️ Managed browser was already closed; skipping shutdown.",
                        Fore.YELLOW,
                    )
                else:
                    self.browser.close()
            except Exception as exc:
                # A page or browser can disappear while a failed stage is
                # being abandoned. Cleanup must not turn that into a second
                # traceback or hide the original stage failure.
                self.log(
                    f"ℹ️ Browser was already unavailable during shutdown ({exc}).",
                    Fore.YELLOW,
                )
        if self.playwright:
            try:
                self.playwright.stop()
            except Exception as exc:
                self.log(
                    f"ℹ️ Playwright was already stopped during shutdown ({exc}).",
                    Fore.YELLOW,
                )
        self.page = None
        self.context = None
        self.browser = None
        self.playwright = None
        self._owns_browser = False