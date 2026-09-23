import os
import shutil
import time
from typing import Optional
from urllib.parse import urlsplit
from urllib.request import urlopen

from patchright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)
from colorama import Fore, Style

from email_provider import EmailProvider
from email_providers import create_email_provider
from pc_flow import (
    classify_session_state,
    classify_signup_state,
    find_validation_errors,
    has_security_challenge_text,
)
from temp_mail import TempMailOrgProvider


class PCAutomation:
    REPLIT_URL = "https://replit.com/"
    SIGNUP_URL = "https://replit.com/signup"
    SIGNUP_RESULT_WAIT_MS = 60_000
    CAPTCHA_SELECTORS = (
        "iframe[title*='captcha' i]",
        "iframe[src*='recaptcha' i]",
        "[data-sitekey]",
        "[id*='captcha' i]",
        "[class*='captcha' i]",
    )
    VALIDATION_SELECTORS = (
        "[role='alert']",
        "[aria-invalid='true']",
        "[data-testid*='error' i]",
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
        self._owns_browser = False

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
        self.page = self._find_page_for_host("replit.com")
        if self.page is None:
            self.page = self._first_open_page()
        if self.page is None:
            self.page = self.context.new_page()
        self.page.set_default_timeout(30_000)
        self.log(
            f"Attached to the existing browser at {cdp_url}; "
            "reusing its open tabs.",
            Fore.GREEN,
        )
        return True

    def _browser_executable_candidates(self) -> list[str]:
        """Find usable Chromium-family browser executables without assuming Edge."""

        candidates = []

        def add(path: Optional[str]) -> None:
            if not path:
                return
            path = os.path.expandvars(os.path.expanduser(path))
            if path not in candidates and os.path.isfile(path):
                candidates.append(path)

        configured_executable = os.environ.get(
            "PLAYWRIGHT_EXECUTABLE_PATH",
            "",
        ).strip()
        if configured_executable:
            add(configured_executable)
            # An explicit executable is an operator choice. Do not silently
            # switch to a different installed browser if its profile is locked.
            return candidates

        if os.name == "nt":
            program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
            program_files_x86 = os.environ.get(
                "PROGRAMFILES(X86)",
                r"C:\Program Files (x86)",
            )
            local_app_data = os.environ.get(
                "LOCALAPPDATA",
                os.path.expanduser(r"~\AppData\Local"),
            )
            for root in (program_files, program_files_x86, local_app_data):
                add(os.path.join(root, "Microsoft", "Edge", "Application", "msedge.exe"))
                add(os.path.join(root, "Google", "Chrome", "Application", "chrome.exe"))
                add(os.path.join(root, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"))
                add(os.path.join(root, "Chromium", "Application", "chromium.exe"))
        else:
            for command in (
                "google-chrome",
                "google-chrome-stable",
                "chromium",
                "chromium-browser",
                "microsoft-edge",
                "brave",
            ):
                add(shutil.which(command))
            add("/repl/tools/bin/chromium")

        return candidates

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
        if page is not None:
            self.page = page
            self.page.set_default_timeout(30_000)
            return page

        if self.context is None:
            raise RuntimeError("Browser context is not ready.")
        page = self.context.new_page()
        page.set_default_timeout(30_000)
        self.page = page
        return page

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

    def _validation_errors(self) -> list[str]:
        # Replit keeps the landing page mounted behind the signup modal. Read
        # the visible dialog/form first so unrelated page text cannot block a
        # valid registration attempt.
        scoped_text = ""
        for scope_selector in ("[role='dialog']", "dialog", "form"):
            scope = self.page.locator(scope_selector)
            try:
                for index in range(scope.count()):
                    candidate = scope.nth(index)
                    if candidate.is_visible(timeout=500):
                        scoped_text = " ".join(candidate.inner_text().split())
                        if scoped_text:
                            break
                if scoped_text:
                    break
            except Exception:
                continue

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

    def handle_captcha(self):
        """Pause for manual security handling and verify it is resolved."""

        if not self._security_challenge_present():
            return True
        self.log("\n⚠️ Security or anti-bot challenge detected.", Fore.YELLOW)
        self.log(
            "🛑 PAUSING: Complete it manually in the browser window; "
            "automation will not solve or retry it.",
            Fore.YELLOW,
        )
        input("👉 Press ENTER here once it is solved...")
        if self._security_challenge_present():
            self._save_failure("captcha_unresolved")
            self.log("❌ Security challenge still appears unresolved.", Fore.RED)
            return False
        self.log("✅ Security challenge no longer visible. Resuming.", Fore.GREEN)
        return True

    def create_account(self, email: Optional[str] = None, password: Optional[str] = None):
        self.log("\n" + "="*60, Fore.MAGENTA)
        self.log("💻 PC: Creating Replit Account", Fore.MAGENTA)
        self.log("="*60, Fore.MAGENTA)
        
        try:
            if email is not None:
                self.email = email
            if password is not None:
                self.password = password
            self.log("\n[1] Navigating to Replit...", Fore.CYAN)
            self._page_for_replit()
            self.page.goto(self.REPLIT_URL, wait_until="domcontentloaded")
            try:
                self.page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeoutError:
                pass

            self.log("[2] Checking for Google One-Tap popup...", Fore.CYAN)
            close_btn = self._visible_control(
                (
                    "button[aria-label='Close']",
                    "[role='dialog'] button[aria-label='Close']",
                    "[role='dialog'] button",
                ),
                timeout=1_500,
            )
            if close_btn is not None:
                close_btn.click()
                self.log("✅ Closed Google popup", Fore.GREEN)
            else:
                self.log("ℹ️ No popup found, continuing...", Fore.YELLOW)
            
            self.log("[3] Clicking 'Create account' or 'Sign up'...", Fore.CYAN)
            signup_btn = self._visible_control(
                (
                    "button:has-text('Create account')",
                    "button:has-text('Sign up')",
                    "a:has-text('Sign up')",
                ),
                timeout=2_000,
            )
            if signup_btn is not None:
                signup_btn.click()
            else:
                self.log(
                    "ℹ️ Create Account was not visible; "
                    "checking whether the account form is already open.",
                    Fore.YELLOW,
                )
            
            self.log("[4] Selecting 'Continue with Email'...", Fore.CYAN)
            email_btn = self._visible_control(
                (
                    "button:has-text('Continue with Email')",
                    "button:has-text('Email')",
                ),
                timeout=2_000,
            )
            if email_btn is not None:
                email_btn.click()
            else:
                self.log("ℹ️ Email option already visible", Fore.YELLOW)
            
            self.log("[5] Entering credentials...", Fore.CYAN)
            email_field = self.page.locator(
                "input[type='email'], input[name='email']"
            ).first
            password_field = self.page.locator(
                "input[type='password'], input[name='password']"
            ).first
            email_field.wait_for(
                state="visible",
                timeout=15_000,
            )
            password_field.wait_for(
                state="visible",
                timeout=15_000,
            )
            email_field.fill(self.email or "")
            password_field.fill(self.password)
            self.log(f"✅ Email: {self.email}", Fore.GREEN)
            self.log("✅ Password entered.", Fore.GREEN)
            
            validation_errors = self._validation_errors()
            if validation_errors:
                self._save_failure("signup_validation_before_submit")
                self.log(
                    f"❌ Registration validation blocked submission "
                    f"({len(validation_errors)} visible error(s)).",
                    Fore.RED,
                )
                return False

            # Handle CAPTCHA before submitting
            if not self.handle_captcha():
                return False
            
            self.log("[6] Submitting form...", Fore.CYAN)
            submit_btn = self._wait_for_control(
                (
                    "[role='dialog'] form button[type='submit']",
                    "form button[type='submit']",
                    "[role='dialog'] button[type='submit']",
                    "button[type='submit']",
                    "[role='dialog'] button:has-text('Create Account')",
                    "form button:has-text('Create Account')",
                    "[role='dialog'] button:has-text('Create account')",
                    "form button:has-text('Create account')",
                ),
                timeout=10_000,
            )
            if submit_btn is None:
                self._save_failure("signup_submit_not_found")
                self.log("❌ Registration submit control was not found.", Fore.RED)
                return False
            # The modal has a second, visually similar header button. Use the
            # form submit control above and verify that the click is accepted.
            submit_url = self.page.url
            submit_btn.click()
            self.log(
                "⏳ Waiting up to 60 seconds for Replit to process signup "
                "before offering manual takeover...",
                Fore.CYAN,
            )

            try:
                self.page.wait_for_function(
                    """(initialUrl) => {
                        const text = document.body?.innerText || "";
                        return /captcha|recaptcha|not a robot|verify you are human/i.test(text) ||
                               /invalid email|email.*required|password.*required|already exists|something went wrong|please enter/i.test(text) ||
                               /check your (email|inbox)|verification email|verify your email|account created/i.test(text) ||
                               location.href !== initialUrl;
                    }""",
                    arg=submit_url,
                    timeout=self.SIGNUP_RESULT_WAIT_MS,
                )
            except PlaywrightTimeoutError:
                self._save_failure("signup_result_timeout")
                self.log("❌ No registration processing/result state was observed.", Fore.RED)
                return False

            state = classify_signup_state(
                self.page.url,
                self._page_text(),
                baseline_url=submit_url,
            )
            if state in {"captcha", "security_challenge"}:
                return self.handle_captcha()
            if state == "validation":
                self._save_failure("signup_validation_after_submit")
                self.log("❌ Registration returned a visible validation error.", Fore.RED)
                return False
            if state != "submitted":
                self._save_failure("signup_result_unknown")
                self.log("❌ Registration result could not be proven.", Fore.RED)
                return False

            self.log("\n✅ Account creation entered an observed result state.", Fore.GREEN)
            return True
            
        except Exception as e:
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
        self.log("\n🛑 Closing browser...", Fore.CYAN)
        if self.context:
            self.save_state()
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