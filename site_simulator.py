"""Local stand-in for replit.com and temp-mail.org, served through Playwright.

``Sim.install(context)`` answers every request a browser context makes to those
sites from this process (``context.route``): there is no server, no TLS
certificate, no port and no browser flag, so it works the same on Windows,
macOS and Linux. Every other http(s) request of that context is aborted and
recorded, so a run can never reach a real site by accident.

The pages reproduce the *behaviour* visible in the project's screenshots, so the
real PCAutomation code can be exercised in a real Chromium:

* the landing page renders after a delay (hydration) with a "Create account"
  button; the sign-up modal sits on an overlay that swallows clicks behind it;
* "Create a Replit account" chooser -> "Create an account" form whose fields
  validate asynchronously ("Email is available" / "Password is valid");
* the reCAPTCHA Enterprise legal notice is shown in every modal;
* submit -> spinner -> "Verify your email to start building" modal;
* optional challenges (a text banner or a cross-origin challenge iframe) that
  only a human action (``solve_captcha``) can clear - nothing here solves one;
* a temp-mail.org style mailbox: address box, Copy/Refresh/Change/Delete
  buttons, an inbox that fills after a delay (div rows or table rows) and the
  message body in an iframe (or inline) with a ``target=_blank`` "Verify Now"
  link and the same link printed as text;
* an optional OLD login (``old_account``): replit.com first shows an empty page and
  moves on to the workspace at ``/~`` a moment later; Settings > Profile is a tall
  page whose "Request account deletion" button sits at the very bottom, opens a
  "Delete Account" dialog, and "Yes, Delete my Account" ends the login and sends
  the browser back to the logged-out home page;
* a single-use ``/action-code`` page: "Verifying email", then "Success! This
  window will close automatically...", and a "link already used" page on any
  second visit.

It does NOT reproduce the real sites' markup, class names, timing or anti-bot
behaviour. Passing against it proves the automation logic in a real browser,
never that the live sites work.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
import traceback
from typing import Any, Optional
from urllib.parse import parse_qs, urlsplit

REPLIT_HOSTS = ("replit.com", "www.replit.com")
MAIL_HOST = "temp-mail.org"
RECAPTCHA_HOST = "www.google.com"

SITE_PATTERN = re.compile(r"^https://(?:www\.)?(?:replit\.com|temp-mail\.org)(?:[/?#]|$)")
RECAPTCHA_PATTERN = re.compile(r"^https://www\.google\.com/recaptcha/")
ANY_WEB_PATTERN = re.compile(r"^https?://", re.IGNORECASE)

NOTICE_HTML = (
    'This site is protected by reCAPTCHA Enterprise and the Google '
    '<a href="#privacy">Privacy Policy</a> and <a href="#terms">Terms of '
    'Service</a> apply.'
)

DEFAULTS: dict[str, Any] = {
    # Replit page
    "hydrate_ms": 1500,          # landing page renders this long after load
    "validate_ms": 900,          # async field validation latency
    "submit_ms": 1200,           # sign-up request latency
    "verify_ms": 600,            # verification request latency
    "early_submit": "accept",    # accept | ignore  (click while validation pending)
    "captcha": "none",           # none | text | frame
    "badge": "hidden",           # hidden | visible  (invisible reCAPTCHA badge)
    "footer_email": False,       # extra email field earlier in the DOM than the modal
    "email_type": "email",       # input type for the email field
    "show_notice": True,         # reCAPTCHA legal notice in modals
    "old_account": "",           # e-mail already signed in at the start ("" = nobody)
    "redirect_ms": 700,          # signed-in visitor of / moves on to /~ after this long
    "delete_ms": 800,            # from "Yes, Delete my Account" to the logged-out home page
    # mailbox page
    "mail_delay_ms": 2500,       # verification mail arrives this long after signup
    "message_view": "iframe",    # iframe | inline
    "rows": "div",               # div | table
    "auto_refresh": True,        # inbox polls by itself; False = only the Refresh button
}


class _Exchange:
    """One HTTP exchange: what the handlers fill in and the route sends back."""

    def __init__(self, request_body: Optional[str]) -> None:
        self.status = 200
        self.headers: dict[str, str] = {}
        self.body = b""
        self._request_body = request_body or ""

    def _send(self, status, body, ctype="text/html; charset=utf-8", headers=None):
        self.status = status
        self.body = body if isinstance(body, bytes) else str(body).encode("utf-8")
        self.headers = {"Content-Type": ctype, "Cache-Control": "no-store"}
        self.headers.update(headers or {})

    def _json(self, payload, status=200, headers=None):
        self._send(status, json.dumps(payload), "application/json", headers)

    def _body(self) -> dict:
        if not self._request_body:
            return {}
        try:
            value = json.loads(self._request_body)
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}


class Sim:
    def __init__(self, **cfg: Any) -> None:
        self.cfg = dict(DEFAULTS)
        unknown = set(cfg) - set(DEFAULTS)
        if unknown:
            raise ValueError(f"unknown simulator options: {sorted(unknown)}")
        self.cfg.update(cfg)
        self.lock = threading.Lock()
        self.t0 = time.monotonic()
        self.events: list[dict] = []
        self.errors: list[str] = []
        self.mailboxes: list[dict] = []
        self.by_address: dict[str, dict] = {}
        self.current: Optional[dict] = None        # the mailbox the page shows
        self.codes: dict[str, dict] = {}           # oobCode -> {email, uses}
        self.captcha_solved = False
        self.session: Optional[str] = self.cfg["old_account"] or None

    # ------------------------------------------------------------ bookkeeping
    def log(self, kind: str, **data: Any) -> None:
        with self.lock:
            self.events.append(
                {"t": round(time.monotonic() - self.t0, 3), "kind": kind, **data}
            )

    def count(self, kind: str, **match: Any) -> int:
        with self.lock:
            return sum(
                1
                for event in self.events
                if event["kind"] == kind
                and all(event.get(key) == value for key, value in match.items())
            )

    def solve_captcha(self) -> None:
        """What the human does: complete the challenge in the browser window."""

        with self.lock:
            self.captcha_solved = True
        self.log("captcha_solved")

    def new_mailbox(self) -> dict:
        """Start a different mailbox; this is what a lost mailbox session looks like.

        The page shows the new address from now on, while mail for an address
        used earlier still lands in that address's own (now invisible) mailbox.
        """

        mailbox = {
            "address": f"sim{secrets.randbelow(90000) + 10000}@airychen.test",
            "messages": [],
        }
        with self.lock:
            self.mailboxes.append(mailbox)
            self.by_address[mailbox["address"].lower()] = mailbox
            self.current = mailbox
        self.log("mailbox_created", address=mailbox["address"])
        return mailbox

    # ---------------------------------------------------------------- routing
    def install(self, context: Any) -> "Sim":
        """Answer the simulated sites from this process and block all else."""

        # Handlers run in the reverse order of registration: the catch-all is
        # registered first so the specific routes below take precedence.
        context.route(ANY_WEB_PATTERN, self._block)
        context.route(SITE_PATTERN, self._route)
        context.route(RECAPTCHA_PATTERN, self._route)
        return self

    def _block(self, route: Any, request: Any) -> None:
        self.log("blocked_request", url=request.url)
        route.abort()

    def _route(self, route: Any, request: Any) -> None:
        try:
            parts = urlsplit(request.url)
            host = (parts.hostname or "").lower()
            try:
                body = request.post_data
            except Exception:
                body = None
            exchange = _Exchange(body)
            query = parse_qs(parts.query)
            post = request.method.upper() == "POST"
            if host == RECAPTCHA_HOST:
                self.recaptcha_get(exchange, parts.path)
            elif host == MAIL_HOST:
                if post:
                    self.mail_post(exchange, parts.path)
                else:
                    self.mail_get(exchange, parts.path, query)
            elif post:
                self.replit_post(exchange, parts.path)
            else:
                self.replit_get(exchange, parts.path, query)
            route.fulfill(
                status=exchange.status, headers=exchange.headers, body=exchange.body
            )
        except Exception:  # a simulator bug must fail loudly, not hang the page
            with self.lock:
                self.errors.append(traceback.format_exc())
            try:
                route.fulfill(status=500, body="simulator error")
            except Exception:
                pass

    # ------------------------------------------------------------------ replit
    def replit_get(self, h: _Exchange, path: str, query: dict) -> None:
        if path == "/~":
            if self.session:
                self.log("workspace_page", email=self.session)
                return h._send(200, WORKSPACE_PAGE)
            return h._send(302, "", headers={"Location": "https://replit.com/"})
        if path == "/settings/profile":
            if not self.session:
                return h._send(302, "", headers={"Location": "https://replit.com/login"})
            self.log("settings_page", email=self.session)
            page = SETTINGS_PAGE.replace("__EMAIL__", self.session).replace(
                "__USER__", re.sub(r"[^a-z0-9]", "", self.session.split("@")[0].lower()) or "user"
            ).replace("__DELETE_MS__", str(int(self.cfg["delete_ms"])))
            return h._send(200, page)
        if path == "/import/github":
            if not self.session:
                self.log("import_page_signed_out")  # the page itself moves a visitor on, like the SPA
                return h._send(200, REDIRECT_TO_LOGIN.replace("__MS__", str(int(self.cfg["redirect_ms"]))))
            self.log("import_page", email=self.session)
            return h._send(
                200, IMPORT_GITHUB_PAGE.replace("__HYDRATE_MS__", str(int(self.cfg["hydrate_ms"])))
            )
        if path == "/login":
            return h._send(200, "<!doctype html><title>Replit</title><h1>Log in to Replit</h1>"
                           "<input type=email placeholder=Email><input type=password placeholder=Password>")
        if path == "/" and self.session:
            self.log("signed_in_home_load")
            return h._send(200, SIGNED_IN_HOME.replace("__MS__", str(int(self.cfg["redirect_ms"]))))
        if path == "/":
            self.log("replit_page_load", authModal=(query.get("authModal") or [""])[0])
            return h._send(200, REPLIT_PAGE.replace("__CFG__", json.dumps(self.cfg)))
        if path == "/action-code":
            code = (query.get("oobCode") or [""])[0]
            self.log("action_code_page", oobCode=code)
            page = ACTION_PAGE.replace("__CFG__", json.dumps(self.cfg)).replace(
                "__CODE__", json.dumps(code)
            )
            return h._send(200, page)
        if path == "/api/verify-code":
            code = (query.get("oobCode") or [""])[0]
            with self.lock:
                entry = self.codes.get(code)
                if entry is None:
                    ok, reason = False, "invalid"
                else:
                    entry["uses"] += 1
                    ok = entry["uses"] == 1
                    reason = "" if ok else "used"
            self.log("verify_attempt", oobCode=code, ok=ok, reason=reason)
            return h._json({"ok": ok, "reason": reason})
        if path == "/api/challenge-state":
            return h._json({"solved": self.captcha_solved})
        return h._send(404, "not found")

    def recaptcha_get(self, h: _Exchange, path: str) -> None:
        if "bframe" in path:
            return h._send(
                200,
                "<html><body style='font:16px sans-serif'>"
                "<h3>Select all images with traffic lights</h3>"
                "<p>(simulated challenge)</p></body></html>",
            )
        return h._send(200, "<html><body></body></html>")

    def replit_post(self, h: _Exchange, path: str) -> None:
        body = h._body()
        if path == "/api/signup":
            email = str(body.get("email") or "")
            self.log(
                "signup_submit",
                email=email,
                password_len=len(str(body.get("password") or "")),
                token=bool(body.get("token")),
            )
            if self.cfg["captcha"] != "none" and not self.captcha_solved:
                self.log("challenge_shown", mode=self.cfg["captcha"])
                return h._json({"status": "challenge", "mode": self.cfg["captcha"]})
            self.deliver_verification_mail(email)
            return h._json({"status": "ok"})
        if path == "/api/delete-account":
            self.log("account_deleted", email=self.session)
            self.session = None
            return h._json({"ok": True})
        if path == "/api/ui-event":
            self.log("replit_ui", **{k: v for k, v in body.items() if k in ("event", "id", "value")})
            return h._json({"ok": True})
        return h._json({"ok": False}, 404)

    def deliver_verification_mail(self, email: str) -> None:
        code = secrets.token_urlsafe(24)
        with self.lock:
            self.codes[code] = {"email": email, "uses": 0}
            mailbox = self.by_address.get(email.lower())
        if mailbox is None:
            self.log("mail_undeliverable", email=email)
            return
        with self.lock:
            mailbox["messages"].append(
                {
                    "id": len(mailbox["messages"]) + 1,
                    "deliver_at": time.monotonic() + self.cfg["mail_delay_ms"] / 1000,
                    "sender": '"Replit" <verify@replit.com>',
                    "subject": "Replit: Verify Your Email",
                    "code": code,
                    "email": email,
                }
            )
        self.log("mail_queued", email=email)

    # --------------------------------------------------------------- temp mail
    def _visible_messages(self, mailbox: dict) -> list[dict]:
        now = time.monotonic()
        with self.lock:
            return [m for m in mailbox["messages"] if m["deliver_at"] <= now]

    def _message_html(self, message: dict) -> str:
        url = (
            "https://replit.com/action-code?mode=verifyEmail&oobCode="
            f"{message['code']}&apiKey=SIMKEY&lang=en"
        )
        local = message["email"].split("@")[0]
        return MESSAGE_BODY.replace("__URL__", url).replace("__LOCAL__", local)

    def mail_get(self, h: _Exchange, path: str, query: dict) -> None:
        mailbox = self.current if self.current is not None else self.new_mailbox()
        if path == "/":
            self.log("mailbox_page_load", address=mailbox["address"])
            page = MAIL_PAGE.replace("__CFG__", json.dumps(self.cfg)).replace(
                "__ADDRESS__", mailbox["address"]
            )
            return h._send(200, page)
        if path == "/api/inbox":
            return h._json(
                {
                    "address": mailbox["address"],
                    "messages": [
                        {"id": m["id"], "sender": m["sender"], "subject": m["subject"]}
                        for m in self._visible_messages(mailbox)
                    ],
                }
            )
        if path.startswith("/api/message/"):
            try:
                ident = int(path.rsplit("/", 1)[1])
            except ValueError:
                return h._json({"error": "bad id"}, 400)
            for message in self._visible_messages(mailbox):
                if message["id"] == ident:
                    return h._json(
                        {
                            "id": ident,
                            "sender": message["sender"],
                            "subject": message["subject"],
                            "html": self._message_html(message),
                        }
                    )
            return h._json({"error": "missing"}, 404)
        return h._send(404, "not found")

    def mail_post(self, h: _Exchange, path: str) -> None:
        body = h._body()
        if path == "/api/ui-event":
            self.log("mail_ui", **{k: v for k, v in body.items() if k in ("event", "id")})
            if body.get("event") == "delete":
                self.new_mailbox()  # the site deletes the mailbox and shows a fresh address
            return h._json({"ok": True})
        return h._json({"ok": False}, 404)


# ----------------------------------------------------------------------- pages
_BASE_CSS = """
*{box-sizing:border-box}
body{margin:0;font:14px/1.4 Inter,Arial,sans-serif;background:#faf8f4;color:#2b2b2b}
header.top{display:flex;align-items:center;gap:24px;padding:14px 60px}
header.top .logo{font-size:20px;font-weight:600;margin-right:20px}
header.top .sp{flex:1}
button{font:inherit;cursor:pointer}
button.pill{border:1px solid #ddd;background:#fff;border-radius:6px;padding:7px 14px}
button.cta{border:0;background:#ff4500;color:#fff;border-radius:8px;padding:8px 14px}
main{text-align:center;padding:90px 20px}
main h1{font-size:44px;margin:0 0 10px}
.prompt{width:480px;margin:30px auto;border:1px solid #f4a58d;border-radius:12px;padding:14px;text-align:left;background:#fff}
.prompt textarea{width:100%;border:0;outline:0;resize:none;background:transparent}
.overlay{position:fixed;inset:0;background:rgba(250,248,244,.75);z-index:50}
.modal{position:fixed;left:50%;top:80px;transform:translateX(-50%);width:360px;background:#fbf7f1;border:1px solid #e6dfd3;
 border-radius:14px;padding:26px 14px 16px;z-index:51;text-align:center;box-shadow:0 12px 40px rgba(0,0,0,.15)}
.modal h2{font-size:19px;font-weight:500;margin:10px 0 18px}
.modal .wide{display:block;width:100%;padding:9px;margin:8px 0;border:1px solid #e8c9ad;background:#fdf6ec;border-radius:6px}
.modal .x{position:absolute;right:12px;top:10px;border:0;background:transparent;font-size:18px}
.modal label{display:block;text-align:left;font-size:11px;margin-top:10px}
.modal .field{position:relative}
.modal input{width:100%;padding:11px;border:1px solid transparent;background:#ecebe7;border-radius:6px}
.modal .state{position:absolute;right:10px;top:11px}
.modal .msg{text-align:left;font-size:11px;min-height:14px;color:#256029}
.modal .msg.err{color:#b3261e}
.modal .submit{width:100%;padding:11px;margin-top:14px;background:#ff3d00;color:#fff;border:0;border-radius:6px}
.modal .submit[disabled]{opacity:.55}
.modal .back{display:block;text-align:left;border:0;background:transparent;margin:6px 0}
.modal .legal{font-size:10px;margin-top:12px}
.modal a{color:#1e6be0}
.spin{display:inline-block;width:12px;height:12px;border:2px solid #aaa;border-top-color:transparent;border-radius:50%;animation:r 0.8s linear infinite}
@keyframes r{to{transform:rotate(360deg)}}
footer{padding:30px 60px;text-align:left}
.grecaptcha-badge{position:fixed;right:0;bottom:70px;width:256px;height:60px;background:#fff;border:1px solid #ccc;z-index:10}
"""

SIGNED_IN_HOME = """<!doctype html><html><head><meta charset="utf-8"><title>Replit</title></head>
<body><script>setTimeout(function () { location.replace('/~'); }, __MS__);</script></body></html>"""

REDIRECT_TO_LOGIN = """<!doctype html><html><head><meta charset="utf-8"><title>Replit</title></head>
<body><script>setTimeout(function () { location.replace('/login'); }, __MS__);</script></body></html>"""

WORKSPACE_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Replit</title></head>
<body style="font:16px sans-serif"><h1>Personal workspace</h1><p>What are we working on today?</p>
<a href="/settings/profile">Settings</a></body></html>"""

# replit.com/import/github as seen in attached_assets/import_from_github_page.png: the
# "GitHub Repo URL" box renders a moment after load; "Connect your GitHub account" and
# "Import from GitHub" must never be clicked by the run (the tests count their clicks).
IMPORT_GITHUB_PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><title>Import from GitHub - Replit</title>
<style>body{font:14px Inter,Arial,sans-serif;background:#f5f3f0;margin:0;padding:40px 280px}
.card{border:1px solid #ddd;border-radius:8px;padding:16px;background:#f9f8f6}
input{width:100%;padding:8px;border:1px solid #ccc;border-radius:6px}
.connect{text-align:center;margin-top:60px} button{padding:8px 12px}</style></head><body>
<button type="button">All import sources</button>
<h1>Import from GitHub</h1>
<p>Import a repository or existing app. Agent support may be limited.</p>
<div class="card" id="card"><p>Loading...</p></div>
<script>
function ev(name, extra) {
  fetch('/api/ui-event', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(Object.assign({event: name}, extra || {}))});
}
setTimeout(function () {
  document.getElementById('card').innerHTML =
    '<label for="repo-url">GitHub Repo URL</label><br>' +
    '<input id="repo-url" type="text" placeholder="https://github.com/username/repo" autocomplete="off">' +
    '<div id="actions"></div><hr><p>Or select a repo from your account</p>' +
    '<div class="connect"><p>Connect to GitHub</p><p>Get started by logging in with your GitHub account</p>' +
    '<button type="button" id="connect">Connect your GitHub account</button></div>';
  var input = document.getElementById('repo-url');
  input.addEventListener('input', function () {
    ev('import_url_typed', {value: input.value});
    var ok = /^https:\/\/github\.com\/[^\/]+\/[^\/]+/.test(input.value);
    document.getElementById('actions').innerHTML = ok ? '<button type="button" id="do-import">Import from GitHub</button>' : '';
    if (ok) { document.getElementById('do-import').addEventListener('click', function () { ev('import_clicked'); }); }
  });
  document.getElementById('connect').addEventListener('click', function () { ev('connect_clicked'); });
}, __HYDRATE_MS__);
</script></body></html>"""

SETTINGS_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Replit</title>
<style>body{font:16px sans-serif;margin:0 40px} .filler{height:2600px}
.overlay{position:fixed;inset:0;background:rgba(0,0,0,.5);display:none}
.overlay.open{display:flex;align-items:center;justify-content:center}
.dialog{background:#fff;padding:24px;width:420px}</style></head><body>
<p>Back to app</p><h1>Profile</h1><p>@__USER__</p>
<p>First name</p><p>Last name</p><p>Location</p><p>Bio</p>
<p>Your email</p><p>__EMAIL__</p><button>Edit</button>
<p>Your password</p><button>Edit</button>
<div class="filler"></div>
<h2>Delete account</h2>
<button id="req" onclick="document.getElementById('dlg').classList.add('open')">Request account deletion</button>
<div class="overlay" id="dlg"><div class="dialog" role="dialog"><h3>Delete Account</h3>
<p>All your hosted Projects and content will be taken down within 24 hours.</p>
<button onclick="document.getElementById('dlg').classList.remove('open')">Cancel</button>
<button id="yes" onclick="yes()">Yes, Delete my Account</button></div></div>
<script>
function yes(){fetch('/api/delete-account',{method:'POST',body:'{}'}).then(function(){
  setTimeout(function(){location.href='/';}, __DELETE_MS__);});}
</script></body></html>"""

REPLIT_PAGE = (
    """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Replit</title>
<style>"""
    + _BASE_CSS
    + """</style></head><body><div id="root"></div>
<script>
const CFG = __CFG__;
const params = new URLSearchParams(location.search);
const NOTICE = CFG.show_notice ? `<p class="legal">""" + NOTICE_HTML + """</p>` : '';
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let modal = null, overlay = null, form = {emailOk:false, pwOk:false, emailPending:false, pwPending:false};
const beacon = (event) => navigator.sendBeacon('/api/ui-event', JSON.stringify(event));

function hydrate() {
  const badge = `<div class="grecaptcha-badge" data-style="bottomright" style="visibility:${CFG.badge === 'visible' ? 'visible' : 'hidden'}">
      <div class="grecaptcha-logo"><iframe title="reCAPTCHA" src="https://www.google.com/recaptcha/enterprise/anchor?size=invisible&badge=bottomright" width="256" height="60"></iframe></div></div>`;
  document.getElementById('root').innerHTML = `
  <header class="top"><span class="logo">Replit</span>
    <span>Products</span><span>Solutions</span><span>Pricing</span><span>Enterprise</span><span>Resources</span>
    <span class="sp"></span><a href="#sales">Contact sales</a>
    <button class="pill" id="hdr-login">Log in</button>
    <button class="cta" id="hdr-create">Create account</button></header>
  <main><h1>What will you build?</h1><p>Turn ideas into apps in minutes — no coding needed</p>
    <div class="prompt"><textarea rows="2" placeholder="Make an app that tracks..."></textarea><span>+</span></div>
    <p>Website &nbsp; Mobile &nbsp; Design &nbsp; Slides &nbsp; Animation</p></main>
  <footer>${CFG.footer_email ? '<form onsubmit="return false"><label>Stay in the loop <input type="email" name="newsletter" placeholder="you@company.com"></label></form>' : ''}</footer>` + badge;
  document.getElementById('hdr-create').onclick = () => openModal('chooser');
  document.getElementById('hdr-login').onclick = () => openModal('chooser');
  if (params.get('authModal') === 'signup') openModal('chooser');
}

function closeModal() { modal && modal.remove(); overlay && overlay.remove(); modal = overlay = null; }

function openModal(step) {
  if (!modal) {
    overlay = document.createElement('div'); overlay.className = 'overlay';
    modal = document.createElement('div'); modal.className = 'modal';
    modal.setAttribute('role', 'dialog'); modal.setAttribute('aria-modal', 'true');
    document.body.appendChild(overlay); document.body.appendChild(modal);
  }
  if (step === 'chooser') {
    modal.innerHTML = `<button class="x" aria-label="Close">×</button><h2>Create a Replit account</h2>
      <button class="wide" id="g">Continue with Google</button>
      <button class="wide" id="email-choice">Continue with Email</button>
      <button class="wide" style="border:0;background:transparent">View more options</button><hr>
      <p>Already have an account? <a href="#login">Log in</a></p>${NOTICE}`;
    modal.querySelector('.x').onclick = closeModal;
    modal.querySelector('#g').onclick = () => beacon({event: 'google_clicked'});
    modal.querySelector('#email-choice').onclick = () => openModal('form');
  } else if (step === 'form') {
    form = {emailOk:false, pwOk:false, emailPending:false, pwPending:false};
    modal.innerHTML = `<button class="x" aria-label="Close">×</button><h2>Create an account</h2>
      <button class="back" id="back">← Back</button>
      <form id="f" novalidate>
        <label for="em">Email</label>
        <div class="field"><input id="em" name="email" type="${CFG.email_type}" autocomplete="email"><span class="state" id="em-s"></span></div>
        <div class="msg" id="em-m"></div>
        <label for="pw">Password</label>
        <div class="field"><input id="pw" name="password" type="password" autocomplete="new-password"><span class="state" id="pw-s"></span></div>
        <div class="msg" id="pw-m"></div>
        <button class="submit" id="submit" type="submit">Create Account</button>
      </form>
      <p>Already have an account? <a href="#login">Log in</a></p>${NOTICE}`;
    modal.querySelector('.x').onclick = closeModal;
    modal.querySelector('#back').onclick = () => openModal('chooser');
    wireForm();
  } else if (step === 'verify') {
    modal.innerHTML = `<h2 style="margin-top:36px">Verify your email to start building</h2>
      <p>An email was sent to ${form.email}. Click the link in that email to verify your account.</p>
      <button class="wide" id="resend">Resend email</button>
      <p style="font-size:11px">Don't see an email? Check your spam folder.<br>If you are unable to verify, <a href="#logout">click here to log out</a>.</p>${NOTICE}`;
  } else if (step === 'challenge-text') {
    modal.innerHTML = `<h2>Create an account</h2><p>Please complete the CAPTCHA to continue.</p>
      <p><label><input type="checkbox" id="robot"> I'm not a robot</label></p>${NOTICE}`;
  } else if (step === 'challenge-frame') {
    modal.innerHTML = `<h2>Create an account</h2><p>Creating your account…</p>${NOTICE}`;
    const frame = document.createElement('iframe');
    frame.title = 'recaptcha challenge expires in two minutes';
    frame.src = 'https://www.google.com/recaptcha/api2/bframe?hl=en';
    frame.style.cssText = 'position:fixed;left:50%;top:60px;margin-left:-200px;width:400px;height:500px;z-index:99;background:#fff;border:1px solid #888';
    frame.id = 'challenge-frame';
    document.body.appendChild(frame);
  }
}

function status(el, text, kind) { el.textContent = text; el.className = 'msg' + (kind === 'err' ? ' err' : ''); }

function wireForm() {
  const em = modal.querySelector('#em'), pw = modal.querySelector('#pw');
  const emM = modal.querySelector('#em-m'), pwM = modal.querySelector('#pw-m');
  const emS = modal.querySelector('#em-s'), pwS = modal.querySelector('#pw-s');
  let t1, t2;
  em.addEventListener('input', () => {
    form.emailOk = false; form.emailPending = true; emM.textContent = ''; emS.innerHTML = '<span class="spin"></span>';
    clearTimeout(t1);
    t1 = setTimeout(() => {
      form.emailPending = false; emS.textContent = '';
      if (/^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$/.test(em.value)) { form.emailOk = true; emS.textContent = '✓'; status(emM, 'Email is available'); }
      else { status(emM, 'Invalid email', 'err'); }
    }, CFG.validate_ms);
  });
  pw.addEventListener('input', () => {
    form.pwOk = false; form.pwPending = true; pwM.textContent = ''; pwS.innerHTML = '<span class="spin"></span>';
    clearTimeout(t2);
    t2 = setTimeout(() => {
      form.pwPending = false; pwS.textContent = '';
      if (pw.value.length >= 8) { form.pwOk = true; pwS.textContent = '✓'; status(pwM, 'Password is valid'); }
      else { status(pwM, 'Password must be at least 8 characters', 'err'); }
    }, CFG.validate_ms);
  });
  modal.querySelector('#f').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const pending = form.emailPending || form.pwPending;
    if (pending && CFG.early_submit === 'ignore') { beacon({event: 'submit_ignored_pending'}); return; }
    if (!pending && (!form.emailOk || !form.pwOk)) { status(emM, em.value ? emM.textContent : 'Email is required', 'err'); return; }
    form.email = em.value;
    const btn = modal.querySelector('#submit');
    btn.disabled = true; btn.innerHTML = '<span class="spin"></span> Create Account';
    await send({email: em.value, password: pw.value});
  });
}

async function send(payload) {
  const r = await fetch('/api/signup', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
  const j = await r.json();
  await sleep(CFG.submit_ms);   // the sign-up request takes this long to answer
  if (j.status === 'challenge') {
    openModal(j.mode === 'frame' ? 'challenge-frame' : 'challenge-text');
    let resent = false;   // polls can overlap; the widget re-sends exactly once
    const timer = setInterval(async () => {
      if (resent) return;
      const s = await (await fetch('/api/challenge-state')).json();
      if (s.solved && !resent) {
        resent = true;
        clearInterval(timer);
        const f = document.getElementById('challenge-frame'); f && f.remove();
        await send({...payload, token: 'solved'});
      }
    }, 300);
  } else if (j.status === 'ok') {
    const f = document.getElementById('challenge-frame'); f && f.remove();
    openModal('verify');
  }
}

setTimeout(hydrate, CFG.hydrate_ms);
</script></body></html>"""
)

ACTION_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Replit</title>
<style>body{font:15px Inter,Arial,sans-serif;background:#f6f5f3;text-align:center;padding-top:60px}
.spin{display:inline-block;width:18px;height:18px;border:2px solid #444;border-top-color:transparent;border-radius:50%;animation:r .8s linear infinite}
@keyframes r{to{transform:rotate(360deg)}}</style></head><body>
<h2 id="h">Verifying email</h2><div id="s"><span class="spin"></span></div>
<script>
const CFG = __CFG__;
const CODE = __CODE__;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
fetch('/api/verify-code?oobCode=' + encodeURIComponent(CODE)).then(r=>r.json()).then(async j=>{
  await sleep(CFG.verify_ms);   // the verification request takes this long to answer
  if (j.ok) {
    setTimeout(()=>{
      document.getElementById('s').innerHTML = '<div style="color:green;font-size:22px">✓</div>' +
        '<p>Success! This window will close automatically in a few seconds…</p><p>Or you can <a href="https://replit.com/">return to Replit here</a>.</p>';
      setTimeout(()=>window.close(), 3500);
    }, 900);
  } else {
    document.getElementById('h').textContent = 'Try verifying your email again';
    document.getElementById('s').innerHTML = '<p>Your request to verify your email has expired or the link has already been used.</p>';
  }
});
</script></body></html>"""

MESSAGE_BODY = """<div style="background:#ececec;padding:20px;font:14px Arial,sans-serif">
<div style="background:#fff;border-radius:18px;padding:26px;max-width:520px;margin:auto">
<p>Hi __LOCAL__,</p><p>Welcome to Replit! We want to make sure that we got your email right.</p>
<p>Please verify your email by clicking the button below:</p>
<p style="text-align:center"><a href="__URL__" target="_blank" rel="noopener"
 style="display:inline-block;background:#0a6cf5;color:#fff;border-radius:6px;padding:10px 22px;text-decoration:none;font-weight:bold">Verify Now</a></p>
<p>- The Replit team</p>
<p style="font-size:10px">If you can't click on the link, copy and paste the following URL into a new tab in your browser:
<a href="__URL__" target="_blank">__URL__</a></p></div></div>"""

MAIL_PAGE = (
    """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Temp Mail - Disposable Temporary Email</title>
<style>
body{margin:0;font:14px Arial,sans-serif;background:#f5f6f8}
.hero{background:#202228;color:#fff;text-align:center;padding:30px 0 50px}
.hero input{width:300px;padding:14px;border-radius:30px;border:0;background:#2b2e36;color:#fff;font-size:17px}
.actions{background:#fff;text-align:center;padding:20px}
.actions button{padding:12px 28px;margin:0 6px;border:0;border-radius:24px;background:#f1f2f4}
.mailbox-wrapper{width:660px;margin:34px auto;background:#fff;border-radius:14px;overflow:hidden;border:1px solid #e1e3e8}
.inbox-head{background:#202228;color:#fff;display:flex;justify-content:space-between;padding:20px 22px;font-weight:bold}
.inbox-list{min-height:360px}
.mail-row{display:flex;justify-content:space-between;align-items:center;padding:14px 22px;background:#f5f6f8;cursor:pointer}
.mail-row .sender{width:200px}.mail-row .subject{flex:1}
table.t{width:100%;border-collapse:collapse}table.t td{padding:14px 22px}table.t tr.row{background:#f5f6f8;cursor:pointer}
.msg-head{padding:16px 22px;border-bottom:1px solid #eee}
iframe.body{width:100%;height:420px;border:0}
</style></head><body>
<div class="hero"><h3>Your Temporary Email Address</h3>
 <input id="mail" readonly value="__ADDRESS__"> <button id="copy-icon" aria-label="Copy" title="Copy">⧉</button></div>
<div class="actions"><button id="copy">Copy</button><button id="refresh">Refresh</button><button id="change">Change</button><button id="delete">Delete</button></div>
<section class="mailbox-wrapper" id="wrap"><div class="inbox-head"><span>SENDER</span><span>SUBJECT</span><span>VIEW</span></div>
 <div class="inbox-list mail-list" id="list"></div></section>
<script>
const CFG = __CFG__;
const beacon = (e) => navigator.sendBeacon('/api/ui-event', JSON.stringify(e));
for (const id of ['copy','refresh','change','delete','copy-icon']) {
  document.getElementById(id).addEventListener('click', () => {
    beacon({event: id});
    if (id === 'copy' || id === 'copy-icon') { navigator.clipboard && navigator.clipboard.writeText(document.getElementById('mail').value).catch(()=>{}); toast('Copied'); }
    if (id === 'refresh') poll();
    if (id === 'delete') setTimeout(() => location.reload(), 300);
  });
}
function toast(t) { const d = document.createElement('div'); d.textContent = t; d.style.cssText='position:fixed;top:10px;right:10px;background:#0a8;color:#fff;padding:8px 14px;border-radius:6px'; document.body.appendChild(d); setTimeout(()=>d.remove(), 1500); }
let opened = false;
async function poll() {
  if (opened) return;
  const j = await (await fetch('/api/inbox')).json();
  const list = document.getElementById('list');
  if (CFG.rows === 'table') {
    list.innerHTML = '<table class="t">' + j.messages.map(m => `<tr class="row" data-id="${m.id}"><td>${esc(m.sender)}</td><td>${esc(m.subject)}</td><td>›</td></tr>`).join('') + '</table>';
    list.querySelectorAll('tr.row').forEach(r => r.onclick = () => open(r.dataset.id));
  } else {
    list.innerHTML = j.messages.map(m => `<div class="mail-row" data-id="${m.id}"><div class="sender">${esc(m.sender)}</div><div class="subject">${esc(m.subject)}</div><div class="view">›</div></div>`).join('');
    list.querySelectorAll('.mail-row').forEach(r => r.onclick = () => open(r.dataset.id));
  }
}
function esc(s) { return s.replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
async function open(id) {
  opened = true; beacon({event: 'message_opened', id});
  const m = await (await fetch('/api/message/' + id)).json();
  const list = document.getElementById('list');
  const head = `<div class="msg-head"><b>${esc(m.sender)}</b><br>Subject: ${esc(m.subject)}</div>`;
  if (CFG.message_view === 'iframe') {
    list.innerHTML = head + '<iframe class="body" id="msgframe"></iframe>';
    document.getElementById('msgframe').srcdoc = m.html;
  } else {
    list.innerHTML = head + '<div class="body-inline">' + m.html + '</div>';
  }
}
poll(); if (CFG.auto_refresh) setInterval(poll, 1000);
</script></body></html>"""
)
