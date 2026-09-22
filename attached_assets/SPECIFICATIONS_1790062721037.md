# SPECIFICATION.md — Exact Step-by-Step Requirements

> This document defines the EXACT sequence of user interactions that must be
> automated. The implementation (in the attached .py files) should match this
> spec as closely as possible. Deviations are noted in "Design Notes" sections.

## PART 1: PC Flow (Steps 1-4)

### Step 1 — Get Temporary Email
1. Obtain a temporary email address.
2. Copy/store it for use in subsequent steps.

**Design Note:** The original spec referenced temp-mail.org, but the implementation
uses 1secmail's public API instead. Reason: temp-mail.org sits behind Cloudflare
and blocks automated access. 1secmail is API-first and works reliably.

### Step 2 — Open Replit & Create Account
1. Navigate to replit.com
2. If a Google sign-in popup appears, close it (× button)
3. Click the "Create Account" button
4. Select "Email" option
5. Enter the temporary email from Step 1
6. Enter password: "12345678"
7. Click "Create Account"
8. Wait for processing/loading

### Step 3 — Verify Email
1. Check the temporary email inbox (poll until email arrives)
2. Look for email from `verify@replit.com` with subject containing "Verify"
3. Extract the verification link (pattern: `https://replit.com/action-code?...`)
4. Open the verification link in the browser
5. Click "Verify Now" if present
6. Wait for success indication

### Step 4 — Session Sync
1. Wait for backend to register the verification
2. Refresh the browser page
3. Confirm logged-in state (or login if needed)

---

## PART 2: Android Flow (Steps 1-24 + Logout)

### Step 1 — Start at Home Screen
- Begin at Android home screen

### Step 2 — Open App Drawer
- Swipe up to open app drawer
- Scroll toward Replit app
- **Preferred**: Scroll to bottom to find Replit (not top)

### Step 3 — Launch Replit
- Click Replit app icon

### Step 4 — Wait for Load
- Wait for app to fully load (splash screen)
- Do not proceed until loaded

### Step 5 — Welcome Screen → Continue
- Locate "Continue" button (preferred over other changing elements)
- Click Continue
- Click "Email" option

### Step 6 — Check Login State
- Check if "Continue with Email" is already active
- If keyboard visible → proceed directly to entering credentials
- If keyboard NOT visible → tap "Continue with Email" or tap outside login area

### Step 7 — Enter Email
- Type the email address from Step 1 (PC flow)
- Use keyboard input

### Step 8 — Tap Continue
- Click Continue button

### Step 9 — Enter Password
- Type password: "12345678"
- Use keyboard input

### Step 10 — Tap Login
- Click Login button

### Step 11 — Handle Login Delay/Error
- Wait for processing
- **Edge case**: If "invalid username/password" banner appears → tap Login again

### Step 12 — Welcome to Replit
- Screen shows: "Welcome to Replit. Let's get started."
- Click Continue

### Step 13 — Loading
- Wait for loading (few seconds)
- Click Continue

### Step 14 — Another Continue
- Click Continue again

### Step 15 — Select Source
- Select "Google Search"
- Click Continue

### Step 16 — Select Role
- Select "Developer"
- Click Continue

### Step 17 — Skip Main Interface
- Click "Skip" (top-right)

### Step 18 — Skip Subscription
- Click "Skip" (top-right)

### Step 19 — Profile Picture
- Open profile picture interface
- May take time to load

### Step 20 — Scroll to Logout
- Wait for profile interface to open
- Scroll down to find "Logout" option

### Step 21 — Tap Logout
- Click "Logout"

### Step 22 — Confirm Logout
- Dialog appears: "Are you sure you want to log out?"
- Options: Cancel / Logout
- Click "Logout"

### Step 23 — Close App
- Force-close the Replit app completely

### Step 24 — Android Flow Complete
- Android portion is done after app is closed

---

## PART 3: PC Resume (Step 5+)

After Android completes:
1. Wait for session sync
2. Refresh browser
3. Login if needed (or confirm already logged in)
4. Import GitHub repository (via AI prompt or sidebar Import flow)

---

## Acceptance Criteria for Implementation

The code MUST:
- ✅ Execute ALL steps in order
- ✅ Handle loading states (wait, don't blind-sleep where possible)
- ✅ Handle edge cases (keyboard shifts, login errors, popups)
- ✅ Use waits/polling instead of hardcoded delays where feasible
- ✅ Provide fallbacks if UI elements aren't found
- ✅ Never skip steps silently — log and fail explicitly

The reviewer should verify that every numbered step above has a corresponding
implementation in the code, with appropriate waits and error handling.