import json
import os
import subprocess
import time

from colorama import Fore, Style, init

from android_automation import AndroidAutomation
from config import (
    ANDROID_DEVICE_ID,
    GITHUB_REPO_URL,
    REPLIT_PASSWORD,
    TEMP_MAIL_PROVIDER,
    TEMP_MAIL_URL,
)
from pc_automation import PCAutomation

init(autoreset=True)

STATE_FILE = "state.json"
STAGES = ["EMAIL", "SIGNUP", "VERIFY", "ANDROID", "PC_LOGIN", "GITHUB", "DONE"]

FRESH_STATE = {
    "stage": "EMAIL",
    "temp_email": None,
    "username": None,
    "password": REPLIT_PASSWORD,
    "verification_link": None,
    "github_repo": GITHUB_REPO_URL,
}


class ReplitAutomationOrchestrator:
    def __init__(self):
        self.state = self._load_state()
        self.android = AndroidAutomation(device_id=ANDROID_DEVICE_ID)
        self.pc = None

    # ---------------- logging & state ----------------

    def log(self, message, color=Fore.WHITE):
        print(f"{color}{message}{Style.RESET_ALL}")

    def _load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict) and loaded.get("stage") in STAGES:
                    return loaded
            except Exception:
                pass
        return dict(FRESH_STATE)

    def _save_state(self, stage):
        self.state["stage"] = stage
        with open(STATE_FILE, "w") as f:
            json.dump(self.state, f, indent=2)
        self.log(f"💾 Checkpoint: next stage = {stage}", Fore.GREEN)

    def reset_state(self):
        self.state = dict(FRESH_STATE)
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)

    # ---------------- browser helper ----------------

    def _ensure_browser(self):
        if self.pc is None:
            self.pc = PCAutomation(
                self.state.get("temp_email"),
                self.state["password"],
                temp_mail_provider=TEMP_MAIL_PROVIDER,
                temp_mail_url=TEMP_MAIL_URL,
            )
            self.pc.setup_browser()

    # ---------------- stages (return True/False) ----------------

    def stage_email(self):
        self.log("\n" + "=" * 60, Fore.MAGENTA)
        self.log("📧 STAGE 1/6: Getting Temporary Email", Fore.MAGENTA)
        self.log("=" * 60, Fore.MAGENTA)
        try:
            self._ensure_browser()
            self.state["temp_email"] = self.pc.obtain_temp_email()
            self.state["username"] = self.state["temp_email"].split("@")[0]
        except Exception as e:
            self.log(f"❌ Failed to get temp email: {e}", Fore.RED)
            return False
        self.log(f"✅ Email: {self.state['temp_email']}", Fore.GREEN)
        self.log(f"👤 Username: {self.state['username']}", Fore.GREEN)
        return True

    def stage_signup(self):
        self.log("\n" + "=" * 60, Fore.MAGENTA)
        self.log("💻 STAGE 2/6: PC Account Creation (browser)", Fore.MAGENTA)
        self.log("=" * 60, Fore.MAGENTA)
        self._ensure_browser()
        return bool(self.pc.create_account())

    def stage_verify(self):
        self.log("\n" + "=" * 60, Fore.MAGENTA)
        self.log("📧 STAGE 3/6: Email Verification", Fore.MAGENTA)
        self.log("=" * 60, Fore.MAGENTA)
        self._ensure_browser()
        self.log("\n⏳ Waiting for the Replit verification message in the mailbox...", Fore.CYAN)
        self.pc.open_verification_message()
        return bool(self.pc.verify_email())

    def stage_android(self):
        self.log("\n" + "=" * 60, Fore.MAGENTA)
        self.log("📱 STAGE 4/6: Android Onboarding (ADB)", Fore.MAGENTA)
        self.log("=" * 60, Fore.MAGENTA)
        result = subprocess.run("adb devices", shell=True, capture_output=True, text=True)
        lines = [l for l in result.stdout.splitlines() if l.strip() and not l.startswith("List")]
        if not any(l.endswith("device") for l in lines):
            self.log("❌ No Android device found via ADB.", Fore.RED)
            self.log("Connect phone, enable USB debugging, accept the prompt.", Fore.YELLOW)
            return False
        self.log("✅ Android device connected!", Fore.GREEN)
        self.log("⏳ Waiting 5s for verification to settle server-side...", Fore.CYAN)
        time.sleep(5)
        return bool(
            self.android.complete_onboarding(
                self.state["temp_email"],
                self.state["password"],
                self.state["username"],
            )
        )

    def stage_pc_login(self):
        self.log("\n" + "=" * 60, Fore.MAGENTA)
        self.log("💻 STAGE 5/6: PC Login After Mobile Onboarding", Fore.MAGENTA)
        self.log("=" * 60, Fore.MAGENTA)
        self._ensure_browser()
        self.log("\n⏳ Waiting for session sync, then refreshing the browser...", Fore.CYAN)
        time.sleep(10)
        try:
            self.pc.page.reload(wait_until="domcontentloaded")
            time.sleep(4)
        except Exception:
            pass
        return bool(self.pc.login_after_mobile())

    def stage_github(self):
        self.log("\n" + "=" * 60, Fore.MAGENTA)
        self.log("📥 STAGE 6/6: GitHub Repository Import", Fore.MAGENTA)
        self.log("=" * 60, Fore.MAGENTA)
        self._ensure_browser()
        repo = self.state.get("github_repo") or ""
        if not repo:
            repo = input("\n📥 Enter GitHub repository URL (Enter to skip): ").strip()
            self.state["github_repo"] = repo
        if not repo:
            self.log("ℹ️ Skipping GitHub import.", Fore.YELLOW)
            return True
        return bool(self.pc.import_github_repo(repo))

    # ---------------- stage runner with recovery menu ----------------

    def run_stage(self, name, fn):
        while True:
            self._save_state(name)  # mark stage as in-progress (crash-safe)
            try:
                ok = bool(fn())
            except Exception as e:
                self.log(f"❌ Stage {name} raised: {e}", Fore.RED)
                ok = False

            if ok:
                return True

            self.log(f"\n⚠️ Stage [{name}] did not complete.", Fore.YELLOW)
            choice = input(
                "Choose: [r]etry / [m]anual takeover then continue / "
                "[s]kip stage / [q]uit: "
            ).strip().lower()

            if choice == "r":
                continue
            if choice == "m":
                input("🖐️ Finish this step by hand (browser/phone). Press ENTER when done...")
                return True
            if choice == "s":
                self.log(f"⏭️ Skipping stage {name}.", Fore.YELLOW)
                return True
            return False

    # ---------------- main runner ----------------

    def run(self):
        start = self.state.get("stage", "EMAIL")

        if start == "DONE":
            again = input(
                "\n🔄 A previous COMPLETED run exists. Start a NEW run? [y/N]: "
            ).strip().lower()
            if again != "y":
                self.log("👋 Exiting.", Fore.GREEN)
                return
            self.reset_state()
            start = "EMAIL"
        elif start not in STAGES:
            start = "EMAIL"
        elif start != "EMAIL":
            self.log(f"\n♻️ Checkpoint found: resuming at stage '{start}'.", Fore.CYAN)
            answer = input(
                "Resume from checkpoint? (y/n — n starts a fresh account): "
            ).strip().lower()
            if answer != "y":
                self.reset_state()
                start = "EMAIL"

        handlers = {
            "EMAIL": self.stage_email,
            "SIGNUP": self.stage_signup,
            "VERIFY": self.stage_verify,
            "ANDROID": self.stage_android,
            "PC_LOGIN": self.stage_pc_login,
            "GITHUB": self.stage_github,
        }

        try:
            for name in STAGES[STAGES.index(start):STAGES.index("DONE")]:
                if not self.run_stage(name, handlers[name]):
                    self.log("\n🛑 Automation aborted by user.", Fore.YELLOW)
                    return

            self._save_state("DONE")
            self.log("\n" + "=" * 60, Fore.GREEN)
            self.log("✅ AUTOMATION COMPLETE!", Fore.GREEN)
            self.log("=" * 60, Fore.GREEN)
            self.log(f"📧 Account: {self.state['temp_email']}")
            self.log(f"👤 Username: {self.state['username']}")
            if self.state.get("github_repo"):
                self.log(f"📥 GitHub: {self.state['github_repo']}")
            self.log("\n💡 For a FRESH account next run, delete state.json and auth_state.json", Fore.CYAN)
            self.log("⏳ Browser stays open 30s for review...")
            time.sleep(30)

        except KeyboardInterrupt:
            self.log("\n\n⚠️ Interrupted by user. Progress saved — rerun to resume.", Fore.YELLOW)
        except Exception as e:
            self.log(f"\n\n❌ Critical error: {e}", Fore.RED)
            import traceback
            traceback.print_exc()
            self.log("💾 Progress saved. Rerun `python main.py` to resume.", Fore.CYAN)
        finally:
            if self.pc:
                self.pc.close_browser()
            self.log("\n👋 Automation session ended.\n", Fore.GREEN)


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print(Fore.MAGENTA + "🚀 REPLIT COMPLETE AUTOMATION (PC Edge + Android ADB)" + Style.RESET_ALL)
    print("=" * 60)
    orchestrator = ReplitAutomationOrchestrator()
    orchestrator.run()