# PART A — PC / Web Flow

## Step 1 — Get the Temporary Email

1. Go to **temp-mail.org**.
2. The temporary email section may initially show a **loading** state.
3. Wait until the temporary email address loads.
4. Once the email address appears, click the **Copy** button.
5. The temporary email address is now copied.

---

## Step 2 — Open Replit

### 2.1 — Open Replit

1. Go to **Replit.com**.
2. A pop-up may appear saying **“Sign in to Replit.com with Google.com.”**
3. Click the **× (cross)** to close the pop-up.

### 2.2 — Close the Pop-up

1. After clicking the cross, the pop-up is closed.
2. If the page takes some time to load or refresh, wait until it finishes.

### 2.3 — Create Account

1. Locate the orange **Create Account** button.
2. Click the **Create Account** button.
3. If the page takes some time to load, wait for it to proceed.

---

## Step 3 — Enter the Account Details

### 3.1 — Select Email

1. Click the **Email** option.
2. If the page takes some time to load, wait until the email fields appear.

### 3.2 — Enter Email and Password

1. Click inside the **Email** field.
2. Press **Ctrl + A**.
3. Press **Ctrl + V** to paste the temporary email address.
4. Click inside the **Password** field.
5. Press **Ctrl + A**.
6. Type **12345678**.
7. Click the **Create Account** button.
8. Replit may rotate/show a **loading** state.
9. Wait for the loading/process to proceed.
10. The screen shown in **4.png** may display a different email address.

---

## Step 4 — Verify the Email

### 4.1 — Return to Temp Mail

1. Go back to the previously opened **temp-mail.org** tab.
2. The inbox may show a **loading** state, so wait if necessary.
3. Once the inbox is visible, look for the Replit verification email:

   * **Sender:** `Replit <verify@replit.com>`
   * **Subject:** `Replit: Verify Your Email`
4. Click the Replit verification email.

### 4.2 — Open the Verification Email

1. The email content may show a **loading** state.
2. Wait until the email content has loaded.
3. Scroll down a little.
4. Locate the visible **Verify Now** option.
5. Click **Verify Now**.

   Some mailbox layouts expose a legacy **Verify Email** control first. If
   that control is present, open it and then continue with **Verify Now**. Do
   not require the legacy control when **Verify Now** is already visible.

### 4.3 — Verify Now

1. The website may take some time to refresh/load.
2. Wait if necessary.
3. Once the verification page opens, click **Verify Now**.

### 4.4 — Verifying Email

1. A new page may open automatically.
2. The page may say **“Verifying email.”**
3. Wait while the verification process takes place.

### 4.5 — Verification Complete

1. Wait for the **email verification tick/success indication**.
2. The window may close automatically or display **“Verifying email success.”**
3. Nothing else needs to be done here.
4. Wait until verification is complete.

---

# PART B — Android Flow

## Android Step 1 — Home Screen

1. We are at the **Android home screen**.

## Android Step 2 — Open the App Drawer

The documented manual procedure is to open the app drawer with a swipe and
select Replit. The current automation intentionally uses the Android launcher
intent for the configured package instead:

```text
adb shell monkey -p com.replit.app -c android.intent.category.LAUNCHER 1
```

This is an implementation shortcut, not a claim that the drawer gesture was
executed. The automation still waits for the observed Replit UI state before
continuing. Use manual takeover if the direct package launch does not expose
the expected screen on a target device.

## Android Step 3 — Open Replit

1. In a manual run, click the **Replit** app from the drawer.
2. In the current automated run, the direct package launch above performs this
   step.

## Android Step 4 — Wait for Replit

1. After clicking Replit, **wait for the app to load**.

## Android Step 5 — Continue and Select Email

1. Once Replit has loaded, locate the **Continue** button.
2. The **Continue** button is the preferred way to proceed because the other elements/options may change.
3. Click **Continue**.
4. Click the **Email** option.

---

## Android Step 6 — Check the Email Login State

1. On the Replit page, check whether the **Continue with Email** option is already active.
2. If the keyboard is already visible, there is no need to do anything else.
3. Use the keyboard to enter the email and credentials.

## Android Step 7 — Enter Email

1. If the keyboard is already visible, type the **email address obtained earlier**.

## Android Step 8 — Keyboard Not Visible

1. In the edge case where the keyboard is **not visible**, and **Continue with Google** and **Continue with Email** are displayed:

   * Click **Continue with Email**.
   * If the login system is still displayed, tap once **outside the login area**.
2. The email field should then become active.
3. The keyboard should appear.

## Android Step 9 — Continue With Email

1. Enter the email address in the **Email** field using the keyboard.
2. Click **Continue**.

## Android Step 10 — Enter Password

1. After clicking **Continue**, enter the password using the keyboard.
2. After entering the password, continue with the login process.

---

## Android Step 11 — Login Processing

1. After entering the password, there may be a **loading/waiting period**.
2. There is also a possibility that the screen displays **invalid username or password** or something similar.
3. Wait for the screen to finish processing.

## Android Step 12 — Login Again if Required

1. If the invalid username/password message appears, click the **Login** button again.

## Android Step 13 — Welcome Screen

1. After successful login, the screen says **“Welcome to Replit. Let’s get started.”**
2. Click **Continue**.

## Android Step 14 — Wait

1. After clicking **Continue**, wait for the loading process.
2. The loading takes at least a couple of seconds.

## Android Step 15 — Continue

1. Once loading has finished, click **Continue**.

---

## Android Step 16 — Continue Again

1. Click **Continue** again.

## Android Step 17 — Select Google Search

1. Select **Google Search**.

## Android Step 18 — Continue

1. Click **Continue**.

## Android Step 19 — Select Developer

1. Select **Developer**.
2. Click **Developer**.

## Android Step 20 — Continue

1. Click **Continue**.

---

## Android Step 21 — Skip

1. Select **Skip**.

## Android Step 22 — Profile Picture Flow

1. After selecting **Skip**, the main flow is complete.
2. The additional profile-picture flow can then be performed.

## Android Step 23 — Profile Picture

1. Take/select the **profile picture**.
2. The profile-picture interface may take some time to open.

## Android Step 24 — Logout

1. Wait for the profile-picture interface to open.
2. Once it opens, scroll as required. (The settings sheet is taller than the screen; the script swipes inside the sheet until **Logout** is visible and never taps Edit Profile. Pass 3; not yet seen on a real phone.)
3. Click **Logout**.

---

# Additional Android Steps — Logout Confirmation

1. After clicking **Logout**, a confirmation appears asking **“Are you sure you want to log out?”**
2. The available options are **Cancel** and **Logout**.
3. Click **Logout**.
4. After logging out, **close the Replit app completely**.

# Temp-mail clean-up (pass 3)

After the Android part (configurable), the script clicks **Delete** on the temp-mail page, because that mailbox is only needed for the verification link. Not yet seen on the live page.

# End of Complete Flow

The process is complete after the Replit app has been closed completely.

## Automation-specific operator notes

Before a live run, use the offline check first:

```bash
python preflight.py
```

To check the browser and ADB installation without starting the flow, use:

```bash
python preflight.py --integration
```

For a real Android run, require exactly one authorized device:

```bash
python preflight.py --require-device
```

CAPTCHA, reCAPTCHA, suspicious-login messages, rate limits, account
restrictions, and device-integrity challenges remain manual. Use the terminal
recovery menu for retry, manual takeover, skip, or quit; do not bypass or
evade the challenge.

The Android automation launches the configured package directly with the
launcher intent documented above. It does not claim to perform the app-drawer
swipe. If the expected screen is not observed, stop for manual takeover.

By default (`RUN_POST_ANDROID_STAGES = False` in `config.py`) the automated
flow ends after the Android stage, which is where this document ends: Replit
is closed on the phone. Set the flag to `True` to opt in to the PC resume and
optional GitHub import that come after it. The saved completion status
distinguishes automated success from manual completion, skipped stages, and
failure.

### Notes taken from the screenshots (not in the numbered steps above)

These are behaviours visible in `attached_assets/` that the numbered steps
leave out. The PC automation relies on them.

- The Replit sign-up is a sequence of modals on the landing page: a chooser
  ("Create a Replit account", with the e-mail option), then the form
  ("Create an account"), then a spinner, then "Verify your email to start
  building". The e-mail and password fields validate asynchronously and show
  "Email is available" / "Password is valid"; the automation waits for both
  before pressing Create account.
- Every one of those modals carries the permanent Google reCAPTCHA legal
  notice ("This site is protected by reCAPTCHA Enterprise and the Google
  Privacy Policy and Terms of Service apply."). That text is not a challenge
  and must not pause the run. An actual challenge (a visible challenge frame, or
  text such as "verify you are human") still pauses for the operator.
- The verification mail has the sender `"Replit" <verify@replit.com>` and the
  subject "Replit: Verify Your Email". It has one **Verify Now** button and the
  same link printed underneath as plain text. The link is single-use, so the
  automation follows it exactly once and falls back to the printed link only if
  the button's own target is rejected.
- The link opens a "Verifying email" page that turns into "Success! This
  window will close automatically in a few seconds…". The automation waits for
  that success text, or for the tab to close by itself after "Verifying email"
  was seen. The step 4.3 wording "click Verify Now" on that page does not match
  any screenshot (4.4 and 4.5 show no button), so no second click is required.
- temp-mail.org shows **Copy**, **Refresh**, **Change** and **Delete** next to
  the inbox. Apart from opening the message, the automation presses only Copy
  and Refresh; Change and Delete would replace or destroy the mailbox the
  verification mail is going to.
