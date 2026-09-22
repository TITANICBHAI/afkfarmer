import time
import requests
import re
from colorama import Fore, Style, init
from pc_automation import PCAutomation
from android_automation import AndroidAutomation
from config import *

# Initialize colorama for colored terminal output
init(autoreset=True)

class ReplitAutomationOrchestrator:
    def __init__(self):
        self.temp_email = None
        self.username = None
        self.password = REPLIT_PASSWORD
        self.verification_link = None
        
        # Initialize components
        self.android = AndroidAutomation(device_id=ANDROID_DEVICE_ID)
        self.pc = None
        
    def log(self, message, color=Fore.WHITE):
        """Log message to console"""
        print(f"{color}{message}{Style.RESET_ALL}")

    def get_temp_email(self):
        """Generate temporary email using 1secmail API"""
        self.log("\n" + "="*60, Fore.MAGENTA)
        self.log("📧 STEP 1: Getting Temporary Email", Fore.MAGENTA)
        self.log("="*60, Fore.MAGENTA)
        
        try:
            response = requests.get("https://www.1secmail.com/api/v1/?action=genRandomMailbox&count=1")
            self.temp_email = response.json()[0]
            self.username = self.temp_email.split('@')[0]
            
            self.log(f"\n✅ Email: {self.temp_email}", Fore.GREEN)
            self.log(f"🔑 Password: {self.password}", Fore.GREEN)
            self.log(f"👤 Username: {self.username}", Fore.GREEN)
            return True
        except Exception as e:
            self.log(f" Failed to get temp email: {e}", Fore.RED)
            return False

    def check_inbox(self):
        """Check for verification email"""
        login, domain = self.temp_email.split('@')
        url = f"https://www.1secmail.com/api/v1/?action=getMessages&login={login}&domain={domain}"
        response = requests.get(url)
        return response.json()

    def read_email(self, msg_id):
        """Read email content"""
        login, domain = self.temp_email.split('@')
        url = f"https://www.1secmail.com/api/v1/?action=readMessage&login={login}&domain={domain}&id={msg_id}"
        response = requests.get(url)
        return response.json()

    def extract_verification_link(self, email_content):
        """Extract verification link from email body"""
        pattern = r'https://replit\.com/action-code[^\s"]+'
        matches = re.findall(pattern, email_content)
        return matches[0] if matches else None

    def run(self):
        """Main automation flow"""
        try:
            # Step 1: Get temporary email
            if not self.get_temp_email():
                return

            # Step 2: Check Android device connection
            self.log("\n" + "="*60, Fore.MAGENTA)
            self.log("📱 STEP 2: Checking Android Device", Fore.MAGENTA)
            self.log("="*60, Fore.MAGENTA)
            
            # Quick ADB check
            import subprocess
            result = subprocess.run("adb devices", shell=True, capture_output=True, text=True)
            if "device" not in result.stdout or result.stdout.count("device") < 2:
                self.log("\n❌ No Android device found via ADB.", Fore.RED)
                self.log("Please connect your phone, enable USB debugging, and run: adb devices", Fore.YELLOW)
                return
            self.log("✅ Android device connected!", Fore.GREEN)

            # Step 3: PC Automation - Create Account on Edge
            self.log("\n" + "="*60, Fore.MAGENTA)
            self.log("💻 STEP 3: PC Account Creation (Edge)", Fore.MAGENTA)
            self.log("="*60, Fore.MAGENTA)
            
            self.pc = PCAutomation(self.temp_email, self.password)
            self.pc.setup_browser()
            
            if not self.pc.create_account():
                self.log("\n Failed to create account on PC", Fore.RED)
                self.pc.close_browser()
                return

            # Step 4: Wait for and verify email
            self.log("\n" + "="*60, Fore.MAGENTA)
            self.log("📧 STEP 4: Email Verification", Fore.MAGENTA)
            self.log("="*60, Fore.MAGENTA)
            
            self.log("\n⏳ Polling inbox for verification email...", Fore.CYAN)
            for attempt in range(EMAIL_CHECK_TIMEOUT // 5):
                time.sleep(5)
                messages = self.check_inbox()
                
                if messages:
                    self.log(f"\n✅ Found email!", Fore.GREEN)
                    msg = messages[0]
                    email_data = self.read_email(msg['id'])
                    
                    self.verification_link = self.extract_verification_link(email_data.get('textBody', ''))
                    if self.verification_link:
                        break
                self.log(".", end="", color=Fore.CYAN)
            
            if not self.verification_link:
                self.log("\n\n❌ Verification email not received in time.", Fore.RED)
                self.pc.close_browser()
                return
            
            # Verify on Edge
            if not self.pc.verify_email(self.verification_link):
                self.log("\n Email verification failed on PC", Fore.RED)
                self.pc.close_browser()
                return

            # Step 5: Android Automation - Complete Onboarding via ADB
            self.log("\n" + "="*60, Fore.MAGENTA)
            self.log("📱 STEP 5: Android Onboarding (ADB)", Fore.MAGENTA)
            self.log("="*60, Fore.MAGENTA)
            
            # Give a moment for the verification to fully register on Replit's servers
            time.sleep(5)
            
            success = self.android.complete_onboarding(
                self.temp_email,
                self.password,
                self.username
            )
            
            if not success:
                self.log("\n⚠️ Android onboarding had issues. Check phone manually.", Fore.YELLOW)
            
            # Step 6: PC - Login and GitHub Import
            self.log("\n" + "="*60, Fore.MAGENTA)
            self.log("💻 STEP 6: PC Login & GitHub Import", Fore.MAGENTA)
            self.log("="*60, Fore.MAGENTA)
            
            # Refresh page to see the main dashboard
            if not self.pc.login_after_mobile():
                self.log("\n⚠️ PC login check failed, but continuing...", Fore.YELLOW)
            
            # Get GitHub repo URL from user
            github_repo = input("\n📥 Enter GitHub repository URL (or press Enter to skip): ").strip()
            
            if github_repo:
                self.pc.import_github_repo(github_repo)
            else:
                self.log("\nℹ️ Skipping GitHub import.", Fore.YELLOW)

            # Final Summary
            self.log("\n" + "="*60, Fore.GREEN)
            self.log("✅ AUTOMATION COMPLETE!", Fore.GREEN)
            self.log("="*60, Fore.GREEN)
            self.log(f"📧 Account: {self.temp_email}")
            self.log(f"🔑 Password: {self.password}")
            self.log(f"👤 Username: {self.username}")
            self.log(f"📱 Android: ✅ Completed via ADB")
            self.log(f"💻 PC (Edge): ✅ Completed")
            if github_repo:
                self.log(f"📥 GitHub: ✅ {github_repo}")
            
            self.log("\n Browser will remain open for 30 seconds for review...")
            time.sleep(30)
            
        except KeyboardInterrupt:
            self.log("\n\n⚠️ Automation interrupted by user.", Fore.YELLOW)
        except Exception as e:
            self.log(f"\n\n❌ Critical error: {e}", Fore.RED)
            import traceback
            traceback.print_exc()
        finally:
            if self.pc:
                self.pc.close_browser()
            self.log("\n👋 Automation session ended.\n", Fore.GREEN)

if __name__ == "__main__":
    print("\n" + "="*60)
    print(Fore.MAGENTA + "🚀 REPLIT COMPLETE AUTOMATION (PC Edge + Android ADB)" + Style.RESET_ALL)
    print("="*60)
    
    orchestrator = ReplitAutomationOrchestrator()
    orchestrator.run()