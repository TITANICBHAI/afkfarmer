import os
import shutil
import time
from typing import Optional

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from colorama import Fore, Style

from pc_flow import classify_signup_state, find_validation_errors, has_captcha_text
from temp_mail import TempMailOrgProvider


class PCAutomation:
    SIGNUP_URL = "https://replit.com/signup"
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
        "[class*='error' i]",
    )

    def __init__(
        self,
        email: Optional[str],
        password: str,
        temp_mail_provider: str = "temp-mail.org",
        temp_mail_url: str = TempMailOrgProvider.URL,
    ):
        self.email = email
        self.password = password
        self.temp_mail_provider = temp_mail_provider
        self.temp_mail_url = temp_mail_url
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.temp_mail = None
        self.state_file = "auth_state.json"

    def log(self, message, color=Fore.WHITE):
        print(f"{color}{message}{Style.RESET_ALL}")

    def setup_browser(self):
        self.log("\n🌐 Setting up the PC browser...", Fore.CYAN)
        self.playwright = sync_playwright().start()

        # Load previous session state if it exists (skips login on future runs)
        storage_state = self.state_file if os.path.exists(self.state_file) else None

        requested_executable = os.environ.get("PLAYWRIGHT_EXECUTABLE_PATH", "").strip()
        edge_available = any(shutil.which(binary) for binary in ("msedge", "microsoft-edge"))
        if requested_executable:
            self.log(
                f"Using configured browser executable: {requested_executable}",
                Fore.CYAN,
            )
            self.browser = self.playwright.chromium.launch(
                executable_path=requested_executable,
                headless=False,
            )
        elif edge_available:
            self.log("Using Microsoft Edge.", Fore.CYAN)
            self.browser = self.playwright.chromium.launch(
                channel=os.environ.get("PLAYWRIGHT_BROWSER_CHANNEL", "msedge"),
                headless=False,
            )
        else:
            chromium_path = "/repl/tools/bin/chromium"
            if not os.path.exists(chromium_path):
                raise RuntimeError(
                    "Microsoft Edge is unavailable and workspace Chromium was not found."
                )
            self.log(
                "Microsoft Edge is unavailable; using workspace Chromium instead.",
                Fore.YELLOW,
            )
            self.browser = self.playwright.chromium.launch(
                executable_path=chromium_path,
                headless=False,
                args=["--no-sandbox"],
            )
        self.context = self.browser.new_context(storage_state=storage_state)
        self.page = self.context.new_page()
        self.page.set_default_timeout(30000)

    def obtain_temp_email(self) -> str:
        """Open temp-mail.org and return an address whose Copy action passed."""

        if self.temp_mail_provider != "temp-mail.org":
            raise ValueError(
                f"Unsupported temporary-mail provider: {self.temp_mail_provider}"
            )
        self._ensure_temp_mail()
        self.email = self.temp_mail.obtain_address()
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

    def open_verification_message(self):
        """Open the Replit verification message in the shared mailbox tab."""

        return self._ensure_temp_mail().open_verification_message()

    def verify_email(self):
        """Follow visible mailbox verification controls and prove success."""

        return self._ensure_temp_mail().verify_email()

    def save_state(self):
        """Save cookies and session state for future runs"""
        try:
            self.context.storage_state(path=self.state_file)
            self.log("💾 Session state saved.", Fore.GREEN)
        except Exception:
            pass

    def _visible_control(self, selectors, timeout=1_500):
        for selector in selectors:
            locator = self.page.locator(selector)
            try:
                for index in range(locator.count()):
                    candidate = locator.nth(index)
                    if candidate.is_visible(timeout=timeout):
                        return candidate
            except Exception:
                continue
        return None

    def _page_text(self) -> str:
        try:
            return self.page.locator("body").inner_text()
        except Exception:
            return ""

    def _validation_errors(self) -> list[str]:
        errors = find_validation_errors(self._page_text())
        for selector in self.VALIDATION_SELECTORS:
            locator = self.page.locator(selector)
            try:
                for index in range(locator.count()):
                    candidate = locator.nth(index)
                    if candidate.is_visible(timeout=500):
                        text = " ".join(candidate.inner_text().split())
                        if text and text not in errors:
                            errors.append(text)
            except Exception:
                continue
        return errors

    def _captcha_present(self) -> bool:
        if has_captcha_text(self._page_text()):
            return True
        return self._visible_control(self.CAPTCHA_SELECTORS, timeout=500) is not None

    def _save_failure(self, tag: str) -> None:
        try:
            os.makedirs("screenshots", exist_ok=True)
            self.page.screenshot(path=os.path.join("screenshots", f"pc_{tag}.png"))
        except Exception:
            pass

    def handle_captcha(self):
        """Pause for manual CAPTCHA handling and verify it is no longer visible."""

        if not self._captcha_present():
            return True
        self.log("\n⚠️ CAPTCHA or anti-bot challenge detected.", Fore.YELLOW)
        self.log(
            "🛑 PAUSING: Solve it manually in the workspace browser window; "
            "automation will not solve or retry it.",
            Fore.YELLOW,
        )
        input("👉 Press ENTER here once it is solved...")
        if self._captcha_present():
            self._save_failure("captcha_unresolved")
            self.log("❌ CAPTCHA still appears unresolved.", Fore.RED)
            return False
        self.log("✅ CAPTCHA no longer visible. Resuming.", Fore.GREEN)
        return True

    def create_account(self):
        self.log("\n" + "="*60, Fore.MAGENTA)
        self.log("💻 PC: Creating Replit Account", Fore.MAGENTA)
        self.log("="*60, Fore.MAGENTA)
        
        try:
            self.log("\n[1] Navigating to Replit signup...", Fore.CYAN)
            self.page.goto(self.SIGNUP_URL, wait_until="domcontentloaded")
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
                self.log("ℹ️ Signup button not found, might already be on the form.", Fore.YELLOW)
            
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
            email_field.wait_for(state="visible", timeout=15_000)
            password_field.wait_for(state="visible", timeout=15_000)
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
            submit_btn = self._visible_control(
                (
                    "button:has-text('Create Account')",
                    "button:has-text('Create account')",
                    "button[type='submit']",
                ),
                timeout=10_000,
            )
            if submit_btn is None:
                self._save_failure("signup_submit_not_found")
                self.log("❌ Registration submit control was not found.", Fore.RED)
                return False
            submit_btn.click()

            try:
                self.page.wait_for_function(
                    """(signupUrl) => {
                        const text = document.body?.innerText || "";
                        return /captcha|recaptcha|not a robot|verify you are human/i.test(text) ||
                               /invalid email|email.*required|password.*required|already exists|something went wrong|please enter/i.test(text) ||
                               /check your (email|inbox)|verification email|verify your email|account created/i.test(text) ||
                               location.href !== signupUrl;
                    }""",
                    arg=self.SIGNUP_URL,
                    timeout=30_000,
                )
            except PlaywrightTimeoutError:
                self._save_failure("signup_result_timeout")
                self.log("❌ No registration processing/result state was observed.", Fore.RED)
                return False

            state = classify_signup_state(self.page.url, self._page_text(), self.SIGNUP_URL)
            if state == "captcha":
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
            self.page.goto("https://replit.com", wait_until="domcontentloaded")
            time.sleep(5)
            
            page_content = self.page.content().lower()
            if "personal workspace" in page_content or "what are we working on today" in page_content or "my repls" in page_content:
                self.log("\n✅ Already logged in! Main interface loaded.", Fore.GREEN)
                self.save_state()
                return True
            
            self.log("[2] Logging in manually...", Fore.CYAN)
            self.page.goto("https://replit.com/login", wait_until="domcontentloaded")
            time.sleep(3)
            
            self.page.fill('input[type="text"], input[type="email"], input[name="email"]', self.email)
            self.page.fill('input[type="password"], input[name="password"]', self.password)
            
            self.handle_captcha()
            
            self.page.locator('button:has-text("Log In"), button[type="submit"]').first.click()
            time.sleep(5)
            self.log("\n✅ Login completed!", Fore.GREEN)
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
            
            # Method 1: Try to find the AI chat input (Replit Agent / Create Repl)
            chat_input = self.page.locator('textarea[placeholder*="Start chatting"], textarea[placeholder*="describe a task"], input[placeholder*="Make an app"]').first
            
            if chat_input.is_visible(timeout=5000):
                self.log("[2] Using AI prompt to import...", Fore.CYAN)
                prompt = f"Import this GitHub repository: {repo_url}"
                chat_input.fill(prompt)
                chat_input.press('Enter')
                
                self.log("\n✅ Import prompt submitted!", Fore.GREEN)
                self.log("⏳ Waiting for Replit to process...", Fore.CYAN)
                time.sleep(15)
                
                current_url = self.page.url
                if "/~/" in current_url:
                    self.log(f"\n✅ Project imported successfully!", Fore.GREEN)
                    self.log(f"📍 URL: {current_url}", Fore.GREEN)
                else:
                    self.log("\nℹ️ Import may still be processing in the background", Fore.YELLOW)
            else:
                self.log("\n⚠️ Could not find AI chat input. Please import manually.", Fore.RED)
            
            return True
            
        except Exception as e:
            self.log(f"\n❌ Error importing repo: {e}", Fore.RED)
            return False

    def close_browser(self):
        self.log("\n🛑 Closing browser...", Fore.CYAN)
        if self.context:
            self.save_state()
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()