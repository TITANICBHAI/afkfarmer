# attached_assets: which file belongs to which step

The folder mixes several series that reuse the same numbers. The extension and the
prefix tell them apart. Nothing in the original series was renamed (the code and
tests refer to those names). Pictures only; no flow logic lives here.

## PC, desktop browser (Edge/Chrome on the PC)

| File | What it shows | Step in `STEPS.md` |
|---|---|---|
| `step_1_*.png` | temp-mail.org with the new address | PC Step 1 |
| `step_2.1_*.png` | replit.com with the Google sign-in pop-up | 2.1 Open Replit |
| `step_2.1a_replit_existing_session_workspace.png` | **conditional** - Replit opens signed in at `https://replit.com/~` (a tilde) | 2.1a |
| `step_2.1b_replit_profile_settings.png` | **conditional** - `https://replit.com/settings/profile` | 2.1b |
| `step_2.1c_replit_request_account_deletion.png` | **conditional** - bottom of that page: "Request account deletion" | 2.1c |
| `step_2.1d_replit_confirm_account_deletion.png` | **conditional** - "Delete Account" dialog, "Yes, Delete my Account" | 2.1d |
| `step_2.1e_replit_logged_out_home.png` | **conditional** - logged-out home page again (Log in / Create account) | 2.1e, then 2.2 |
| `step_2.2_*.png`, `step_2.3_*.png` | pop-up closed; "Create an account" dialog | 2.2, 2.3 |
| `3.1_*.png`, `3.2_*.png`, `3.3_*.png` | sign-up form, field checks | 3.1-3.2 |
| `4_*.png` (png) | "Verify your email to start building" | after 3.2 |
| `4.1_*.png` ... `4.5_*.png` | mailbox, verification mail, "Verifying email" | 4.1-4.5 |
| `IMG-20260922-WA0000_*.jpg` | sign-up dialog opened by `?authModal=signup` | reference |

## Android, Replit app (`.jpg`, number = screenshot number)

| File | Screen |
|---|---|
| `1_` ... `3_` | home screen, app drawer, Replit starting |
| `4_*.jpg` (jpg) | "Create anything" with Continue |
| `5_`, `6_` | sign-in options, "Continue With Email" |
| `7_` ... `11_` | e-mail, then password |
| `12_`, `13_` | login processing, "Invalid username or password" |
| `14_` | "Welcome to Replit! Let's get started" |
| `15_` | "What's your name?" (first name is pre-filled; only Continue is tapped) |
| `16_` | "Hi <name>! Let's create a username" (only Continue is tapped) |
| `17_`, `18_` | "How did you hear about Replit?", "What best describes you?" |
| `19_` | "Start your building journey" with **Skip** |
| `19.5_1790062961803.jpg` | **conditional** - "Notifications" sheet: tap **Not Now** (never "Turn On Notifications") |
| `20_` | "What are we working on today?" (profile avatar top right) |
| `21_`, `22_` | profile/settings sheet: top, then scrolled to **Log Out** |
| `23_` | "Are you sure you want to log out?" |
| `24_` | Android home screen after Replit was closed |

Note: `18_1790062961803.jpg` and `19_1790062961803.jpg` share the same number after
the underscore. That is how they were uploaded; the part before the underscore is
the screenshot number.

## Chrome on Android (`0.*`)

`0.1` Google, `0.2`-`0.4` address bar, `0.5` replit.com home, `0.6.1` home with
Log in / Create account, `0.6` the same with the Google pop-up, `0.7` sign-up
dialog, `0.8` e-mail field. This is a mobile-browser series; it is
NOT the PC series. (Earlier drafts named the new PC screenshots `0.5.1`-`0.5.5`,
which collides with `0.5`; they are `step_2.1a`-`step_2.1e` now.)

## Not pictures

The `Pasted-*.txt`, `Qwen_*`, `REVIEW_*`, `SPECIFICATION*` and `requirements_*`
files are notes and prompts written for earlier work. They are not part of the flow.
