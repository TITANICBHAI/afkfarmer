"""Configuration settings for Replit Automation"""

# --- Replit Account Settings ---
REPLIT_PASSWORD = "12345678"

# --- Email Settings ---
EMAIL_STRATEGY = "hybrid"  # hybrid, api, or temp-mail.org
PRIMARY_EMAIL_API = "1secmail"
USER_CUSTOM_EMAIL = ""

# --- Android Settings ---
REPLIT_PACKAGE_NAME = "com.replit.app"
ANDROID_DEVICE_ID = None  # Leave as None to auto-detect

# --- Timing Settings (in seconds) ---
EMAIL_CHECK_TIMEOUT = 60       # Max time to wait for verification email
ANDROID_STEP_DELAY = 2.0       # Delay between ADB taps/typing
PAGE_LOAD_TIMEOUT = 15         # Max time for a webpage to load
LOGIN_RETRY_DELAY = 3          # Delay before retrying login if it fails
ELEMENT_WAIT_TIMEOUT = 10      # Max seconds to wait for a UI element on the phone

# --- Temp Mail Settings ---
TEMP_MAIL_PROVIDER = "temp-mail.org"
TEMP_MAIL_URL = "https://temp-mail.org/"

# --- GitHub Import ---
GITHUB_REPO_URL = ""  # Optional: pre-fill a repo URL to skip the manual prompt
RUN_POST_ANDROID_STAGES = False  # CORE_FLOW.md ends when Replit is closed on the phone;
                                # set True to also run PC login + optional GitHub import

# --- PC browser ---
# "persistent": the run opens its own Chrome/Edge window with a saved profile
# folder and LEAVES IT OPEN when the script ends, so the account stays logged in.
# "managed": the old behaviour (a throw-away window that closes with the script).
# Chrome 136+ ignores remote debugging for your everyday profile, so this is a
# separate profile. Environment overrides: AFK_BROWSER_MODE / _PROFILE_DIR / _DEBUG_PORT.
BROWSER_MODE = "persistent"
BROWSER_PROFILE_DIR = "browser_profile"
BROWSER_DEBUG_PORT = 9333

# --- Step detection ---
# True: look at the real browser / phone to find the current step (and where in
# the step) instead of trusting only the saved checkpoint.
AUTO_DETECT_STAGE = True
# A Replit login that is already open in the browser when sign-up starts:
# "ask" (default), "signout" (own browser profile only) or "keep".
EXISTING_SESSION_ACTION = "ask"

# --- Temporary mailbox ---
# The temp-mail address only exists to receive the verification link. When it is
# no longer needed the run clicks the page's Delete button:
#   "after_android" (default, once the phone part is done), "after_verify"
#   (right after the link was followed) or "never".
DELETE_TEMP_MAIL = "after_android"

# --- Attention ---
ALERT_SOUND = True  # beep when the run needs you (CAPTCHA pause, failed-stage menu)

# --- Logging ---
DEBUG_MODE = True