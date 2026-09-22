import time
import os
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from colorama import Fore, Style

class PCAutomation:
    def __init__(self, email, password):
        self.email = email
        self.password = password
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.state_file = "auth_state.json"

    def log(self, message, color=Fore.WHITE):
        print(f"{color}{message}{Style.RESET_ALL}")

    def setup_browser(self):
        self.log("\n🌐 Setting up Microsoft Edge...", Fore.CYAN)
        self.playwright = sync_playwright().start()
        
        # Load previous session state if it exists (skips login on future runs)
        storage_state = self.state_file if os.path.exists(self.state_file) else None
        
        self.browser = self.playwright.chromium.launch(channel="msedge", headless=False)
        self.context = self.browser.new_context(storage_state=storage_state)
        self.page = self.context.new_page()
        self.page.set_default_timeout(30000)

    def save_state(self):
        """Save cookies and session state for future runs"""
        try:
            self.context.storage_state(path=self.state_file)
            self.log("💾 Session state saved.", Fore.GREEN)
        except Exception:
            pass

    def handle_captcha(self):
        """Detect reCAPTCHA and pause for manual solving"""
        try:
            # Check for reCAPTCHA iframe or checkbox
            captcha_frame = self.page.frame_locator("iframe[title*='reCAPTCHA']").first
            if captcha_frame:
                self.log("\n⚠️ reCAPTCHA detected!", Fore.YELLOW)
                self.log("🛑 PAUSING: Please solve the CAPTCHA manually in the browser window.", Fore.YELLOW)
                input("👉 Press ENTER here once you have solved it...")
                self.log("✅ CAPTCHA solved. Resuming...", Fore.GREEN)
                return True
        except Exception:
            pass # No CAPTCHA found
        return False

    def create_account(self):
        self.log("\n" + "="*60, Fore.MAGENTA)
        self.log("💻 PC: Creating Replit Account", Fore.MAGENTA)
        self.log("="*60, Fore.MAGENTA)
        
        try:
            self.log("\n[1] Navigating to Replit signup...", Fore.CYAN)
            self.page.goto("https://replit.com/signup", wait_until="domcontentloaded")
            
            # Wait for page to be interactive
            self.page.wait_for_load_state("networkidle")
            time.sleep(2)
            
            self.log("[2] Checking for Google One-Tap popup...", Fore.CYAN)
            try:
                close_btn = self.page.locator('button[aria-label="Close"], div[role="dialog"] button[aria-label]').first
                if close_btn.is_visible(timeout=3000):
                    close_btn.click()
                    self.log("✅ Closed Google popup", Fore.GREEN)
                    time.sleep(1)
            except PlaywrightTimeoutError:
                self.log("ℹ️ No popup found, continuing...", Fore.YELLOW)
            
            self.log("[3] Clicking 'Create account' or 'Sign up'...", Fore.CYAN)
            try:
                signup_btn = self.page.locator('button:has-text("Create account"), button:has-text("Sign up"), a:has-text("Sign up")').first
                signup_btn.click(timeout=10000)
                time.sleep(2)
            except PlaywrightTimeoutError:
                self.log("ℹ️ Signup button not found, might already be on the form.", Fore.YELLOW)
            
            self.log("[4] Selecting 'Continue with Email'...", Fore.CYAN)
            try:
                email_btn = self.page.locator('button:has-text("Continue with Email"), button:has-text("Email")').first
                email_btn.click(timeout=5000)
                time.sleep(2)
            except PlaywrightTimeoutError:
                self.log("ℹ️ Email option already visible", Fore.YELLOW)
            
            self.log("[5] Entering credentials...", Fore.CYAN)
            self.page.fill('input[type="email"], input[name="email"]', self.email)
            self.page.fill('input[type="password"], input[name="password"]', self.password)
            self.log(f"✅ Email: {self.email}", Fore.GREEN)
            self.log(f"✅ Password: {self.password}", Fore.GREEN)
            
            # Handle CAPTCHA before submitting
            self.handle_captcha()
            
            self.log("[6] Submitting form...", Fore.CYAN)
            self.page.locator('button:has-text("Create Account"), button[type="submit"]').first.click()
            
            # Wait for navigation or error message
            time.sleep(5)
            self.log("\n✅ Account creation form submitted!", Fore.GREEN)
            return True
            
        except Exception as e:
            self.log(f"\n❌ Error creating account: {e}", Fore.RED)
            return False

    def verify_email(self, verification_link):
        self.log("\n" + "="*60, Fore.MAGENTA)
        self.log("📧 PC: Verifying Email", Fore.MAGENTA)
        self.log("="*60, Fore.MAGENTA)
        
        try:
            self.log(f"\n[1] Opening verification link...", Fore.CYAN)
            self.page.goto(verification_link, wait_until="domcontentloaded")
            time.sleep(4)
            
            self.log("[2] Clicking 'Verify Now'...", Fore.CYAN)
            try:
                verify_btn = self.page.locator('button:has-text("Verify Now"), a:has-text("Verify")').first
                verify_btn.click(timeout=10000)
                self.log("✅ Verification submitted", Fore.GREEN)
            except PlaywrightTimeoutError:
                self.log("ℹ️ 'Verify Now' button not found or already verified", Fore.YELLOW)
            
            self.log("[3] Waiting for verification to complete...", Fore.CYAN)
            time.sleep(6)
            
            try:
                if self.page.locator('text=Success').first.is_visible(timeout=5000):
                    self.log("\n✅ Email verified successfully!", Fore.GREEN)
                else:
                    self.log("\nℹ️ Verification page processed", Fore.YELLOW)
            except PlaywrightTimeoutError:
                self.log("\nℹ️ Verification page processed", Fore.YELLOW)
            
            return True
            
        except Exception as e:
            self.log(f"\n❌ Error verifying email: {e}", Fore.RED)
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