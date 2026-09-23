import json
import os
import tempfile
import time
from datetime import datetime, timezone

from colorama import Fore, Style, init

from android_automation import AndroidAutomation
from config import (
    ANDROID_DEVICE_ID,
    EMAIL_CHECK_TIMEOUT,
    EMAIL_STRATEGY,
    GITHUB_REPO_URL,
    PRIMARY_EMAIL_API,
    REPLIT_PASSWORD,
    TEMP_MAIL_PROVIDER,
    TEMP_MAIL_URL,
    USER_CUSTOM_EMAIL,
)
from pc_automation import PCAutomation

init(autoreset=True)

STATE_FILE = "state.json"
STAGES = ["EMAIL", "SIGNUP", "VERIFY", "ANDROID", "PC_LOGIN", "GITHUB", "DONE"]
PASSWORD_REF = "config.REPLIT_PASSWORD"


def fresh_state():
    """Return a new redacted state record for one run."""
    return {
        "stage": "EMAIL",
        "in_progress": None,
        "temp_email": None,
        "email_provider": None,
        "username": None,
        "password_ref": PASSWORD_REF,
        "verification_link": None,
        "github_repo": GITHUB_REPO_URL,
        "decisions": [],
    }


FRESH_STATE = fresh_state()


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
                    state = fresh_state()
                    state.update(loaded)
                    # Migrate old checkpoints without carrying the password
                    # into the persisted state or browser-resume metadata.
                    state.pop("password", None)
                    state["password_ref"] = PASSWORD_REF
                    if not isinstance(state.get("decisions"), list):
                        state["decisions"] = []
                    return state
            except Exception:
                pass
        return fresh_state()

    @staticmethod
    def _timestamp():
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def _state_payload(self):
        payload = dict(self.state)
        payload.pop("password", None)
        payload["password_ref"] = PASSWORD_REF
        return payload

    def _write_state_atomically(self):
        state_path = os.path.abspath(STATE_FILE)
        state_dir = os.path.dirname(state_path) or "."
        fd, temp_path = tempfile.mkstemp(
            prefix=".state.",
            suffix=".tmp",
            dir=state_dir,
            text=True,
        )
        try:
            os.chmod(temp_path, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(self._state_payload(), f, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, state_path)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def _save_state(self, stage, *, in_progress=None, outcome=None):
        self.state["stage"] = stage
        self.state["in_progress"] = in_progress
        if outcome:
            self.state.setdefault("decisions", []).append(
                {
                    "stage": outcome["stage"],
                    "outcome": outcome["outcome"],
                    "at": outcome.get("at", self._timestamp()),
                }
            )
        self._write_state_atomically()
        status = " in progress" if in_progress else ""
        self.log(f"Checkpoint: next stage = {stage}{status}", Fore.GREEN)

    def _next_stage(self, stage):
        index = STAGES.index(stage)
        return STAGES[index + 1]

    def _record_outcome(self, stage, outcome):
        self._save_state(
            stage,
            in_progress=self.state.get("in_progress"),
            outcome={"stage": stage, "outcome": outcome},
        )

    def reset_state(self):
        self.state = fresh_state()
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)

    # ---------------- browser helper ----------------

    def _ensure_browser(self):
        if self.pc is None:
            saved_provider = self.state.get("email_provider")
            strategy = EMAIL_STRATEGY
            custom_email = USER_CUSTOM_EMAIL
            if saved_provider == "temp-mail.org":
                strategy = "temp-mail.org"
            elif saved_provider == "1secmail":
                strategy = "api"
            elif saved_provider == "custom":
                custom_email = self.state.get("temp_email") or USER_CUSTOM_EMAIL
            self.pc = PCAutomation(
                self.state.get("temp_email"),
                REPLIT_PASSWORD,
                temp_mail_provider=TEMP_MAIL_PROVIDER,
                temp_mail_url=TEMP_MAIL_URL,
                email_strategy=strategy,
                primary_email_api=PRIMARY_EMAIL_API,
                user_custom_email=custom_email,
                email_check_timeout=EMAIL_CHECK_TIMEOUT,
            )
            self.pc.setup_browser()

    # ---------------- stages (return True/False) ----------------

    def stage_email(self):
        self.log("\n" + "=" * 60, Fore.MAGENTA)
        self.log("📧 STAGE 1/6: Getting Email", Fore.MAGENTA)
        self.log("=" * 60, Fore.MAGENTA)
        try:
            self._ensure_browser()
            self.state["temp_email"] = self.pc.obtain_temp_email()
            self.state["email_provider"] = self.pc.email_provider_name
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
        verified = bool(self.pc.verify_email())
        if verified:
            self.state["verification_link"] = self.pc.verification_url
        return verified

    def stage_android(self):
        self.log("\n" + "=" * 60, Fore.MAGENTA)
        self.log("📱 STAGE 4/6: Android Onboarding (ADB)", Fore.MAGENTA)
        self.log("=" * 60, Fore.MAGENTA)
        if not self.android.verify_device():
            self.log("❌ Intended Android device was not verified.", Fore.RED)
            self.log("Connect the operator-owned phone and verify its configured ID.", Fore.YELLOW)
            return False
        return bool(
            self.android.complete_onboarding(
                self.state["temp_email"],
                REPLIT_PASSWORD,
                self.state["username"],
            )
        )

    def stage_pc_login(self):
        self.log("\n" + "=" * 60, Fore.MAGENTA)
        self.log("💻 STAGE 5/6: PC Login After Mobile Onboarding", Fore.MAGENTA)
        self.log("=" * 60, Fore.MAGENTA)
        self._ensure_browser()
        self.log("\n⏳ Waiting for Android session synchronization...", Fore.CYAN)
        session_state = self.pc.wait_for_session_sync()
        if session_state == "unknown":
            self.log(
                "❌ Browser did not expose an authenticated or login-required state "
                "after synchronization probes.",
                Fore.RED,
            )
            return False
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
            self._save_state(
                name,
                in_progress={
                    "stage": name,
                    "status": "running",
                    "started_at": self._timestamp(),
                },
            )
            try:
                ok = bool(fn())
            except Exception as e:
                self.log(f"❌ Stage {name} raised: {e}", Fore.RED)
                ok = False

            if ok:
                self._save_state(self._next_stage(name))
                return True

            self._save_state(
                name,
                in_progress={
                    "stage": name,
                    "status": "failed",
                    "observed_at": self._timestamp(),
                },
            )
            self.log(f"\n⚠️ Stage [{name}] did not complete.", Fore.YELLOW)
            choice = input(
                "Choose: [r]etry / [m]anual takeover then continue / "
                "[s]kip stage / [q]uit: "
            ).strip().lower()

            if choice == "r":
                self._record_outcome(name, "retry")
                continue
            if choice == "m":
                input("🖐️ Finish this step by hand (browser/phone). Press ENTER when done...")
                self._record_outcome(name, "manual_takeover")
                self._save_state(self._next_stage(name))
                return True
            if choice == "s":
                self.log(f"⏭️ Skipping stage {name}.", Fore.YELLOW)
                self._record_outcome(name, "skip")
                self._save_state(self._next_stage(name))
                return True
            self._record_outcome(name, "quit")
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
    print(Fore.MAGENTA + "🚀 REPLIT COMPLETE AUTOMATION (PC Browser + Android ADB)" + Style.RESET_ALL)
    print("=" * 60)
    orchestrator = ReplitAutomationOrchestrator()
    orchestrator.run()