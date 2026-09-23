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

# --- Logging ---
DEBUG_MODE = True