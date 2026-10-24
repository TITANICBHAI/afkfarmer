"""Scripted browser doubles for the offline tests.

They model what the screenshots show: a landing page that hydrates late, a
chooser modal, a credentials form whose fields validate asynchronously, a
mailbox whose message arrives late and may live in an iframe, and a one-time
verification tab. Time is a ``VirtualClock`` that only moves when the code under
test waits, so a 60-second timeout costs no real time and every run is
deterministic.

What these doubles prove is control flow and ordering (what is clicked, how
often, after what). They cannot prove that the live Replit or mailbox sites look
like this; test_site_simulation.py drives a real Chromium against a local
imitation of the pages, and only a manual run proves the real sites.
"""

from __future__ import annotations

from typing import Any, Callable, Optional


def _resolve(value: Any) -> Any:
    return value() if callable(value) else value


class VirtualClock:
    """Stand-in for the ``time`` module inside the modules under test.

    ``limit_s`` is a watchdog: code that keeps waiting far longer than any real
    timeout is a loop that never gives up, and the test should fail, not hang.
    """

    def __init__(self, start: float = 1_000.0, limit_s: float = 3_600.0) -> None:
        self.start = start
        self.now = start
        self.limit_s = limit_s

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, float(seconds))
        if self.now - self.start > self.limit_s:
            raise RuntimeError(
                "virtual time ran away: the code under test never gave up waiting"
            )

    def advance_ms(self, milliseconds: float) -> None:
        self.sleep(milliseconds / 1_000)

    @property
    def elapsed(self) -> float:
        return self.now - self.start


class Node:
    """One element and, at the same time, the locator that points at it."""

    def __init__(
        self,
        name: str = "",
        *,
        visible: Any = True,
        text: Any = "",
        href: Any = None,
        enabled: Any = True,
        present: Any = True,
        on_click: Optional[Callable[[], None]] = None,
        click_error: Optional[Exception] = None,
    ) -> None:
        self.name = name
        self.present_rule = present
        self.visible_rule = visible
        self.text_rule = text
        self.href = href
        self.enabled_rule = enabled
        self.on_click = on_click
        self.click_error = click_error
        self.click_attempts = 0
        self.clicks = 0

    @property
    def first(self) -> "Node":
        return self

    def count(self) -> int:
        return 1 if _resolve(self.present_rule) else 0

    def nth(self, _index: int) -> "Node":
        return self

    def is_visible(self, timeout: Any = None) -> bool:
        return bool(_resolve(self.visible_rule))

    def is_enabled(self, timeout: Any = None) -> bool:
        return bool(_resolve(self.enabled_rule))

    def inner_text(self) -> str:
        return str(_resolve(self.text_rule))

    def get_attribute(self, name: str) -> Any:
        return _resolve(self.href) if name == "href" else None

    def click(self, **_kwargs: Any) -> None:
        self.click_attempts += 1
        if not self.is_visible():
            # Playwright waits for the element and then times out.
            raise TimeoutError(f"{self.name or 'element'} is not visible")
        if self.click_error is not None:
            raise self.click_error
        self.clicks += 1
        if self.on_click is not None:
            self.on_click()


class NoNodes:
    """A selector that matches nothing."""

    @property
    def first(self) -> "NoNodes":
        return self

    def count(self) -> int:
        return 0

    def nth(self, _index: int) -> Any:
        raise IndexError("no element matches")

    def is_visible(self, timeout: Any = None) -> bool:
        return False

    def is_enabled(self, timeout: Any = None) -> bool:
        return False

    def click(self, **_kwargs: Any) -> None:
        raise TimeoutError("no element matches")

    def inner_text(self) -> str:
        raise TimeoutError("no element matches")

    def get_attribute(self, _name: str) -> Any:
        return None

    def evaluate_all(self, _script: str) -> list:
        return []


class Field(Node):
    """A text input. It can be told to lose what is typed (a framework reset)."""

    def __init__(
        self,
        name: str,
        *,
        wipes: int = 0,
        keeps: bool = True,
        on_change: Optional[Callable[["Field"], None]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(name, **kwargs)
        self.value = ""
        self.fills = 0
        self.wipes_left = wipes
        self.keeps = keeps
        self.on_change = on_change

    def fill(self, value: str) -> None:
        self.fills += 1
        if not self.keeps:
            self.value = ""
        elif self.wipes_left > 0:
            self.wipes_left -= 1
            self.value = ""
        else:
            self.value = value
        if self.on_change is not None:
            self.on_change(self)

    def input_value(self) -> str:
        return self.value


# --------------------------------------------------------------------------- #
# Replit sign-up (landing -> chooser modal -> credentials form -> result)      #
# --------------------------------------------------------------------------- #


class FakeReplitSignup:
    """The sign-up pages from the screenshots, on a virtual clock.

    Knobs
    -----
    hydrate_s       seconds after loading until the landing controls exist
                    (None: never, as on a page whose script did not start)
    deep_link       what ``?authModal=signup`` does: "chooser" or "nothing"
    email_option    whether the chooser offers "Continue with Email"
    validation_s    seconds after both fields are filled until Replit's checks
                    finish (None: never); the spinner shows no text meanwhile
    validation_error  a line shown instead of "Email is available"/
                    "Password is valid" once the checks finish
    outcome         what submitting does: "verify", "workspace" (the new account
                    is signed in at /~), "error" or "nothing"
    challenge       None, "text" (words in the page) or "frame" (an iframe)
    challenge_when  "submit" (appears after the click), "fill" or "load"
    solvable        whether the operator's manual solve works
    challenge_repeats  how many times the challenge comes back after a solve
    human_s         seconds after a challenge appears until a person finishes it by
                    themselves (None: nobody does; only an ENTER prompt or the test)
    rearm_after_s   after a solve, seconds until the next round shows up
                    (None: at the very next wait)
    click_errors    {control name: exception raised by its click}
    field_wipes     fills the form throws away before keeping one
    fields_keep_value  False: the form never keeps what is typed
    marker_text     text of a visible aria-invalid/alert marker
    popup           None, "closes" or "close_fails" (the Google one-tap close)
    start_state     where the tab is when the run looks at it: "blank" (default),
                    "landing", "chooser", "form", "result" (check-your-email) or
                    "authenticated" (already logged in)
    logged_in       the site keeps a login: loading it shows the workspace (at
                    ``/~``) until the cookies are cleared or the account is deleted
    settings_email / settings_username   what Settings > Profile shows
    logout_delay_s  seconds from "Yes, Delete my Account" until Replit is back on
                    the logged-out home page
    logout_lingers  True: confirming the deletion never logs out by itself
    delete_button   False: the settings page has no "Request account deletion"
    home_redirect_s seconds a logged-in visitor of replit.com sees an empty page
                    before Replit moves on to the workspace at ``/~``
    """

    LANDING = (
        "Replit\nProducts\nPricing\nLog in\nCreate account\n"
        "What will you build today?"
    )
    NOTICE = (
        "This site is protected by reCAPTCHA Enterprise and the Google "
        "Privacy Policy and Terms of Service apply."
    )
    DEEP_LINK = "https://replit.com/?authModal=signup"

    def __init__(
        self,
        clock: VirtualClock,
        *,
        hydrate_s: Optional[float] = 0.0,
        deep_link: str = "chooser",
        email_option: bool = True,
        validation_s: Optional[float] = 2.0,
        validation_error: Optional[str] = None,
        outcome: str = "verify",
        challenge: Optional[str] = None,
        challenge_when: str = "submit",
        solvable: bool = True,
        challenge_repeats: int = 0,
        human_s: Optional[float] = None,
        rearm_after_s: Optional[float] = None,
        click_errors: Optional[dict] = None,
        field_wipes: int = 0,
        fields_keep_value: bool = True,
        marker_text: Optional[str] = None,
        popup: Optional[str] = None,
        start_state: str = "blank",
        logged_in: bool = False,
        settings_email: str = "old.account@example.test",
        settings_username: str = "oldaccount",
        logout_delay_s: float = 3.0,
        logout_lingers: bool = False,
        delete_button: bool = True,
        home_redirect_s: float = 0.0,
    ) -> None:
        # Imported here so this module stays importable without patchright.
        from pc_automation import PCAutomation

        self._pc = PCAutomation
        self.clock = clock
        self.hydrate_s = hydrate_s
        self.deep_link = deep_link
        self.email_option_present = email_option
        self.validation_s = validation_s
        self.validation_error = validation_error
        self.outcome = outcome
        self.challenge = challenge
        self.challenge_when = challenge_when
        self.solvable = solvable
        self.repeats_left = challenge_repeats
        self.human_s = human_s
        self.rearm_after_s = rearm_after_s
        self.marker_text = marker_text
        self.popup = popup

        self.logged_in = logged_in or start_state == "authenticated"
        self.url = "about:blank" if start_state == "blank" else "https://replit.com/"
        if start_state == "authenticated":
            self.url = "https://replit.com/~"
        self.settings_email = settings_email
        self.settings_username = settings_username
        self.home_redirect_s = home_redirect_s
        self.logout_delay_s = logout_delay_s
        self.logout_lingers = logout_lingers
        self.logout_at: Optional[float] = None
        self.deleted = False
        self.state = start_state  # blank | landing | chooser | form | result | authenticated
        self.navigated_at = clock.now
        self.filled_at: Optional[float] = None
        self.submit_at: Optional[float] = None
        self.challenge_active = False
        self.rearm_at: Optional[float] = None
        self.shown_at: Optional[float] = None
        self.front_calls = 0
        self.challenge_solves = 0
        self.error_line: Optional[str] = None
        self.popup_closed = False
        self.goto_urls: list[str] = []
        self.events: list[tuple[float, str]] = []
        self.screenshot_paths: list[str] = []

        errors = click_errors or {}
        self.create_account = Node(
            "create_account",
            visible=self._create_account_visible,
            on_click=self._on_create_account,
            click_error=errors.get("create_account"),
        )
        self.email_option = Node(
            "email_option",
            visible=lambda: self.state == "chooser" and self.email_option_present,
            on_click=self._on_email_option,
            click_error=errors.get("email_option"),
        )
        self.email = Field(
            "email",
            visible=lambda: self.state == "form",
            on_change=self._on_fill,
            wipes=field_wipes,
            keeps=fields_keep_value,
        )
        self.password = Field(
            "password",
            visible=lambda: self.state == "form",
            on_change=self._on_fill,
            wipes=field_wipes,
            keeps=fields_keep_value,
        )
        self.submit = Node(
            "submit",
            visible=lambda: self.state == "form",
            on_click=self._on_submit,
            click_error=errors.get("submit"),
        )
        self.challenge_frame = Node(
            "challenge_frame",
            visible=lambda: self.challenge_active and self.challenge == "frame",
        )
        self.marker = Node(
            "marker",
            visible=lambda: self.marker_text is not None and self.state == "form",
            text=lambda: self.marker_text or "",
        )
        self.popup_close = Node(
            "popup_close",
            visible=lambda: (
                self.popup is not None
                and self.state == "landing"
                and not self.popup_closed
            ),
            on_click=self._on_popup_close,
            click_error=RuntimeError("element was detached")
            if popup == "close_fails"
            else None,
        )
        self.dialog = Node(
            "dialog",
            visible=lambda: self.state in {"chooser", "form", "result", "delete_dialog"},
            text=self.dialog_text,
        )
        self.delete_request = Node(
            "request_account_deletion",
            visible=lambda: self.state == "settings" and delete_button,
            present=lambda: self.state == "settings" and delete_button,
            on_click=self._on_request_deletion,
        )
        self.delete_confirm = Node(
            "yes_delete_my_account",
            visible=lambda: self.state == "delete_dialog",
            present=lambda: self.state == "delete_dialog",
            on_click=self._on_confirm_deletion,
        )
        self.body = Node("body", text=self.body_text)

    # -- page API used by PCAutomation ------------------------------------- #

    def set_default_timeout(self, _milliseconds: int) -> None:
        return None

    def is_closed(self) -> bool:
        return False

    def goto(self, url: str, wait_until: Optional[str] = None) -> None:
        self.goto_urls.append(url)
        self.url = url
        self.navigated_at = self.clock.now
        self.challenge_active = False
        if url.startswith("https://replit.com/settings"):
            if self.logged_in:
                self.state = "settings"
            else:
                self.state, self.url = "login", "https://replit.com/login"
        elif self.logged_in:
            if self.home_redirect_s > 0:
                self.state = "redirecting"  # nothing on screen yet, still at replit.com/
            else:
                self.state = "authenticated"
                self.url = "https://replit.com/~"
        elif url.startswith(self.DEEP_LINK):
            self.state = "chooser" if self.deep_link == "chooser" else "landing"
        else:
            self.state = "landing"
        if self.challenge and self.challenge_when == "load" and not self.challenge_solves:
            self.show_challenge()

    def wait_for_load_state(self, _state: str = "load", timeout: int = 0) -> None:
        return None

    def wait_for_timeout(self, milliseconds: float) -> None:
        self.clock.advance_ms(milliseconds)
        if (
            self.state == "redirecting"
            and self.clock.now - self.navigated_at >= self.home_redirect_s
        ):
            self.state, self.url = "authenticated", "https://replit.com/~"
        if self.logout_at is not None and self.clock.now >= self.logout_at:
            self.logout_at = None
            self.logged_in = False
            self.deleted = True
            self.state, self.url = "landing", "https://replit.com/"
            self.navigated_at = self.clock.now
            self._log("logged out after the deletion")
        if self.rearm_at is not None and self.clock.now >= self.rearm_at:
            self.rearm_at = None
            self.show_challenge("challenge returned")
        if (
            self.human_s is not None
            and self.challenge_active
            and self.shown_at is not None
            and self.clock.now - self.shown_at >= self.human_s
        ):
            self.solve_challenge()

    def screenshot(self, path: Optional[str] = None, **_kwargs: Any) -> None:
        self.screenshot_paths.append(str(path))

    def locator(self, selector: str) -> Any:
        pc = self._pc
        if selector == "body":
            return self.body
        if selector in ("[role='dialog']", "dialog", "dialog[open]"):
            return self.dialog
        if selector in (
            "button[aria-label='Close']",
            "[role='dialog'] button[aria-label='Close']",
        ):
            return self.popup_close
        if selector in pc.REQUEST_DELETION_SELECTORS:
            return self.delete_request
        if selector in pc.CONFIRM_DELETION_SELECTORS:
            return self.delete_confirm
        if selector in pc.CAPTCHA_SELECTORS:
            return self.challenge_frame
        if selector in pc.VALIDATION_SELECTORS:
            return self.marker
        if selector in pc.PASSWORD_FIELD_SELECTORS:
            return self.password
        if selector in pc.EMAIL_FIELD_SELECTORS:
            return self.email
        if selector in pc.EMAIL_CHOICE_SELECTORS:
            return self.email_option
        if selector in pc.CREATE_ACCOUNT_SELECTORS:
            return self.create_account
        if selector in pc.SUBMIT_SELECTORS:
            return self.submit
        return NoNodes()

    # -- operator ---------------------------------------------------------- #

    def show_challenge(self, label: str = "challenge shown") -> None:
        """The page puts a challenge on screen (what a site does by itself)."""

        self.challenge_active = True
        self.shown_at = self.clock.now
        self._log(label)

    def bring_to_front(self) -> None:
        self.front_calls += 1

    def solve_challenge(self) -> None:
        """What a person does when the terminal asks them to finish the challenge."""

        self.challenge_solves += 1
        if not self.solvable:
            return
        self.challenge_active = False
        self._log("challenge solved")
        if self.repeats_left > 0:
            self.repeats_left -= 1
            self.rearm_at = self.clock.now + (self.rearm_after_s or 0.0)
            return
        if self.submit_at is not None:
            self._apply_outcome()

    # -- rendering --------------------------------------------------------- #

    def _log(self, label: str) -> None:
        self.events.append((self.clock.elapsed, label))

    def event_labels(self) -> list[str]:
        return [label for _when, label in self.events]

    def validated_at(self) -> Optional[float]:
        if self.filled_at is None or self.validation_s is None:
            return None
        return self.filled_at + self.validation_s

    def _hydrated(self) -> bool:
        return (
            self.hydrate_s is not None
            and self.clock.now - self.navigated_at >= self.hydrate_s
        )

    def _validated(self) -> bool:
        moment = self.validated_at()
        return moment is not None and self.clock.now >= moment

    def validation_lines(self) -> list[str]:
        lines: list[str] = []
        if self.error_line:
            lines.append(self.error_line)
        if self._validated():
            if self.validation_error:
                lines.append(self.validation_error)
            else:
                lines.extend(["Email is available", "Password is valid"])
        return lines

    def dialog_text(self) -> str:
        if self.state == "chooser":
            lines = [
                "Create a Replit account",
                "Continue with Google",
                "Continue with Email",
                "View more options",
            ]
        elif self.state == "form":
            lines = [
                "Create an account",
                "Back",
                "Email",
                "Password",
                *self.validation_lines(),
                "Create Account",
            ]
        elif self.state == "result":
            lines = ["Verify your email to start building"]
        elif self.state == "delete_dialog":
            lines = [
                "Delete Account",
                "All your hosted Projects and content will be taken down within 24 hours.",
                "This cannot be undone.",
                "Cancel",
                "Yes, Delete my Account",
            ]
        else:
            lines = []
        if self.challenge_active and self.challenge == "text":
            lines.append("Please complete the CAPTCHA to continue")
        lines.append(self.NOTICE)
        return "\n".join(lines)

    def settings_text(self) -> str:
        return (
            "Back to app\nPersonal workspace\nProfile\n"
            f"@{self.settings_username}\nFirst name\nLast name\nLocation\nBio\n"
            f"Your email\n{self.settings_email}\nEdit\nYour password\nEdit\n"
            "Sign out of all devices\nDelete account\n"
            + ("Request account deletion" if self.delete_request.is_visible() or self.state == "delete_dialog" else "")
        )

    def body_text(self) -> str:
        if self.state == "redirecting":
            return ""
        if self.state == "authenticated":
            return "Personal workspace\nWhat are we working on today?"
        if self.state == "settings":
            return self.settings_text()
        if self.state == "delete_dialog":
            return self.settings_text() + "\n" + self.dialog_text()
        if self.state == "login":
            return "Log in to Replit"
        parts = [self.LANDING]
        if self.state in {"chooser", "form", "result"}:
            parts.append(self.dialog_text())
        elif self.challenge_active and self.challenge == "text":
            parts.append("Please complete the CAPTCHA to continue")
        return "\n".join(parts)

    # -- behaviour --------------------------------------------------------- #

    def _create_account_visible(self) -> bool:
        if self.state == "landing":
            return self._hydrated()
        # The header button stays mounted behind the modal.
        return self.state in {"chooser", "form", "result"}

    def _on_create_account(self) -> None:
        if self.state == "landing":
            self.state = "chooser"
            self._log("chooser opened")
        else:
            self._log("header button pressed behind a modal")

    def _on_email_option(self) -> None:
        self.state = "form"
        self._log("form opened")

    def _on_request_deletion(self) -> None:
        self.state = "delete_dialog"
        self._log("deletion dialog opened")

    def _on_confirm_deletion(self) -> None:
        self._log("deletion confirmed")
        if not self.logout_lingers:
            self.logout_at = self.clock.now + self.logout_delay_s

    def _on_popup_close(self) -> None:
        self.popup_closed = True
        self._log("popup closed")

    def _on_fill(self, _field: Field) -> None:
        self._log(f"fill {_field.name}")
        both = bool(self.email.value) and bool(self.password.value)
        if not both:
            self.filled_at = None
            return
        if self.filled_at is None:
            self.filled_at = self.clock.now
            if self.challenge and self.challenge_when == "fill":
                self.show_challenge()

    def _on_submit(self) -> None:
        self.submit_at = self.clock.now
        self._log("submit")
        if self.challenge and self.challenge_when == "submit":
            self.show_challenge()
            return
        self._apply_outcome()

    def _apply_outcome(self) -> None:
        if self.outcome == "verify":
            self.state = "result"
            self._log("result shown")
        elif self.outcome == "workspace":
            # The brand-new account is signed in straight away (at /~).
            self.state, self.url, self.logged_in = "authenticated", "https://replit.com/~", True
            self._log("new account signed in")
        elif self.outcome == "error":
            self.error_line = "Something went wrong. Please try again."
        # "nothing": the page just sits there.


# --------------------------------------------------------------------------- #
# Mailbox (temp-mail.org) and the one-time verification tab                    #
# --------------------------------------------------------------------------- #


class Handle:
    """What ``page.evaluate_handle`` returns."""

    def __init__(self, element: Any) -> None:
        self._element = element

    def as_element(self) -> Any:
        return self._element


class RowHandle:
    """An element handle on the inbox listing, as ``evaluate_handle`` gives it.

    It dies when the inbox redraws itself (the next click on it fails with
    "Element is not attached to the DOM"), and a checked click first waits for
    the element to be stable, which takes as long as the tab is slow to draw.
    """

    def __init__(self, tab: "FakeMailTab") -> None:
        self.tab = tab
        self.generation = tab.redraw_count()

    def _attached(self) -> bool:
        return self.generation == self.tab.redraw_count()

    def click(self, **kwargs: Any) -> None:
        tab = self.tab
        tab.row_handle_clicks += 1
        tab.actions.append("row.click")
        if not tab.in_front and tab.starved_frames_s:
            tab.clock.sleep(tab.starved_frames_s)  # waiting for it to be stable
        if not self._attached():
            raise RuntimeError(
                "Element is not attached to the DOM\n"
                "Call log:\n  - waiting for element to be visible, enabled and stable"
            )
        tab.row.click(**kwargs)

    def bounding_box(self) -> Optional[dict]:
        if self._attached() and self.tab._listed():
            return dict(self.tab.ROW_BOX)
        return None


class FakeMouse:
    """``page.mouse``: a click lands on whatever is at the point."""

    def __init__(self, tab: "FakeMailTab") -> None:
        self.tab = tab
        self.clicks: list[tuple[float, float]] = []
        self.error: Optional[Exception] = None

    def click(self, x: float, y: float, **_kwargs: Any) -> None:
        self.tab.actions.append("mouse.click")
        if self.error is not None:
            raise self.error
        self.clicks.append((x, y))
        box = self.tab.ROW_BOX
        inside = (
            box["x"] <= x <= box["x"] + box["width"]
            and box["y"] <= y <= box["y"] + box["height"]
        )
        # A row that something lies on top of never receives the click.
        if inside and self.tab._listed() and self.tab.row.click_error is None:
            self.tab._on_row_click()


class FakeTab:
    """A Replit verification tab whose text follows a script of (delay, text)."""

    DEFAULT_SCRIPT = (
        (0.0, "Verifying email"),
        (1.0, "Success! This window will close automatically in a few seconds..."),
    )

    def __init__(
        self,
        context: "FakeContext",
        clock: VirtualClock,
        url: str = "about:blank",
        script: Any = None,
        closes_after_s: Optional[float] = None,
    ) -> None:
        self.context = context
        self.clock = clock
        self.url = url
        self.script = list(script if script is not None else self.DEFAULT_SCRIPT)
        self.closes_after_s = closes_after_s
        self.opened_at = clock.now
        self.closed = False
        self.goto_calls: list[str] = []
        context.pages.append(self)

    def _age(self) -> float:
        return self.clock.now - self.opened_at

    def is_closed(self) -> bool:
        if not self.closed and self.closes_after_s is not None:
            self.closed = self._age() >= self.closes_after_s
        return self.closed

    def _text(self) -> str:
        if self.is_closed():
            raise RuntimeError("Target page, context or browser has been closed")
        current = ""
        for delay, text in self.script:
            if self._age() >= delay:
                current = text
        return current

    def locator(self, selector: str) -> Any:
        return Node("body", text=self._text) if selector == "body" else NoNodes()

    def goto(self, url: str, wait_until: Optional[str] = None) -> None:
        self.goto_calls.append(url)
        self.url = url
        self.opened_at = self.clock.now

    def wait_for_load_state(self, _state: str = "load", timeout: int = 0) -> None:
        return None

    def wait_for_timeout(self, milliseconds: float) -> None:
        self.clock.advance_ms(milliseconds)

    def screenshot(self, path: Optional[str] = None, **_kwargs: Any) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class FakeContext:
    """A browser context: a list of tabs plus the ability to open new ones."""

    def __init__(
        self,
        clock: VirtualClock,
        verification_script: Any = None,
        tab_closes_after_s: Optional[float] = None,
    ) -> None:
        self.clock = clock
        self.pages: list[Any] = []
        self.verification_script = verification_script
        self.tab_closes_after_s = tab_closes_after_s
        self.opened_urls: list[str] = []

    def new_page(self) -> FakeTab:
        return FakeTab(
            self,
            self.clock,
            "about:blank",
            self.verification_script,
            self.tab_closes_after_s,
        )

    def open_verification_tab(self, url: str) -> FakeTab:
        self.opened_urls.append(url)
        return FakeTab(
            self,
            self.clock,
            url,
            self.verification_script,
            self.tab_closes_after_s,
        )

    def grant_permissions(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class FakeFrame:
    """The main frame of a mailbox page, or an iframe holding the message."""

    def __init__(
        self,
        text: Any = "",
        html: Any = "",
        controls: Optional[dict] = None,
    ) -> None:
        self.text_rule = text
        self.html_rule = html
        self.controls = controls or {}

    @staticmethod
    def _group(selector: str) -> Optional[str]:
        from temp_mail import TempMailOrgProvider as Provider

        if selector in Provider.VERIFY_NOW_SELECTORS:
            return "verify_now"
        if selector in Provider.VERIFY_EMAIL_SELECTORS:
            return "verify_email"
        if selector == "a[href*='action-code']":
            return "action_link"
        if selector in Provider.REFRESH_SELECTORS:
            return "refresh"
        return None

    def locator(self, selector: str) -> Any:
        if selector == "body":
            return Node("body", text=self.text_rule)
        group = self._group(selector)
        node = self.controls.get(group) if group else None
        return node if node is not None else NoNodes()

    def content(self) -> str:
        return str(_resolve(self.html_rule))


class FakeMailTab(FakeFrame):
    """The temp-mail.org tab.

    Knobs
    -----
    mail_at_s        seconds until the Replit message exists (None: never)
    needs_refresh    the inbox only lists it after the Refresh control is used
    message_view     "iframe" (the message body is a child frame) or "inline"
    verify_control   "verify_now", "verify_email" (legacy first button) or "none"
    legacy_first_click  for "verify_email": "starts" (opens the link),
                     "starts_and_reveals" (opens it and also shows Verify Now),
                     "reveals_now" (shows Verify Now but opens nothing) or "nothing"
    href             the link behind the control
    text_url         whether the message also prints a link as text
    text_href        the printed link (default: same as href)
    row_opens / subject_opens / open_delay_s   how clicking the listing behaves
    shown_address    the address the page displays (default: the account's)
    redraw_every_s   the inbox replaces its elements this often (None: never)
    starved_frames_s while the tab is not in front, a checked click on an element
                     handle waits this long to see it stable before it can act
    front_helps      False: bring_to_front() does not make the tab the active one
    """

    URL = "https://temp-mail.org/"
    VALID_HREF = "https://replit.com/action-code?mode=verifyEmail&oobCode=abc123"
    ROW_BOX = {"x": 120.0, "y": 240.0, "width": 420.0, "height": 48.0}

    def __init__(
        self,
        context: FakeContext,
        clock: VirtualClock,
        *,
        address: str = "someone@example.test",
        shown_address: Optional[str] = None,
        mail_at_s: Optional[float] = 0.0,
        needs_refresh: bool = False,
        message_view: str = "iframe",
        verify_control: str = "verify_now",
        legacy_first_click: str = "starts",
        href: Optional[str] = None,
        text_href: Optional[str] = None,
        text_url: bool = True,
        row_opens: bool = True,
        subject_opens: bool = True,
        open_delay_s: float = 0.5,
        click_error: Optional[Exception] = None,
        redraw_every_s: Optional[float] = None,
        starved_frames_s: float = 0.0,
        front_helps: bool = True,
    ) -> None:
        self.context = context
        self.clock = clock
        self.url = self.URL
        self.redraw_every_s = redraw_every_s
        self.starved_frames_s = starved_frames_s
        self.front_helps = front_helps
        self.in_front = False
        self.front_calls = 0
        self.row_handle_clicks = 0
        self.actions: list[str] = []
        self.mouse = FakeMouse(self)
        self.address = address
        self.shown_address = shown_address if shown_address is not None else address
        self.mail_at_s = mail_at_s
        self.needs_refresh = needs_refresh
        self.message_view = message_view
        self.verify_control = verify_control
        self.legacy_first_click = legacy_first_click
        self.href = href if href is not None else self.VALID_HREF
        self.text_href = text_href if text_href is not None else self.href
        self.text_url = text_url
        self.row_opens = row_opens
        self.subject_opens = subject_opens
        self.open_delay_s = open_delay_s
        self.created_at = clock.now
        self.refreshes = 0
        self.opened_at: Optional[float] = None
        self.now_revealed = False
        self.screenshot_paths: list[str] = []
        context.pages.append(self)

        self.row = Node(
            "row",
            visible=self._listed,
            text="verify@replit.com Replit: Verify Your Email",
            on_click=self._on_row_click,
        )
        self.subject = Node(
            "subject",
            visible=self._listed,
            text="Replit: Verify Your Email",
            on_click=self._on_subject_click,
        )
        self.refresh = Node("refresh", on_click=self._on_refresh)

        # These exist in the page (count() > 0) only while the message is shown.
        verify_now_shown = lambda: self._message_open() and (
            self.verify_control == "verify_now" or self.now_revealed
        )
        verify_email_shown = lambda: (
            self._message_open() and self.verify_control == "verify_email"
        )
        link_shown = lambda: self._message_open() and self.verify_control != "none"
        self.verify_now = Node(
            "verify_now",
            visible=verify_now_shown,
            present=verify_now_shown,
            href=lambda: self.href,
            on_click=self._open_link,
            click_error=click_error,
        )
        self.verify_email = Node(
            "verify_email",
            visible=verify_email_shown,
            present=verify_email_shown,
            href=lambda: self.href,
            on_click=self._on_legacy_click,
            click_error=click_error,
        )
        self.action_link = Node(
            "action_link",
            visible=link_shown,
            present=link_shown,
            href=lambda: self.href,
        )
        message_controls = {
            "verify_now": self.verify_now,
            "verify_email": self.verify_email,
            "action_link": self.action_link,
        }
        self.message_frame = FakeFrame(
            text=self._message_text,
            html=self._message_html,
            controls=message_controls,
        )
        super().__init__(
            text=self._main_text,
            html=self._main_html,
            controls={"refresh": self.refresh},
        )
        if self.message_view == "inline":
            self.controls.update(message_controls)

    # -- page API ----------------------------------------------------------- #

    @property
    def frames(self) -> list[Any]:
        if self.message_view == "iframe" and self._message_open():
            return [self, self.message_frame]
        return [self]

    def is_closed(self) -> bool:
        return False

    def goto(self, url: str, wait_until: Optional[str] = None) -> None:
        self.url = url

    def wait_for_load_state(self, _state: str = "load", timeout: int = 0) -> None:
        return None

    def wait_for_timeout(self, milliseconds: float) -> None:
        self.clock.advance_ms(milliseconds)

    def screenshot(self, path: Optional[str] = None, **_kwargs: Any) -> None:
        self.screenshot_paths.append(str(path))

    def bring_to_front(self) -> None:
        self.front_calls += 1
        self.actions.append("front")
        if self.front_helps:
            self.in_front = True

    def redraw_count(self) -> int:
        """How many times the inbox has replaced its elements so far."""

        if not self.redraw_every_s:
            return 0
        return int((self.clock.now - self.created_at) / self.redraw_every_s)

    def evaluate_handle(self, _script: str, _argument: Any = None) -> Handle:
        return Handle(RowHandle(self) if self._listed() else None)

    def get_by_text(self, _pattern: Any, **_kwargs: Any) -> Any:
        return self.subject if self._listed() else NoNodes()

    # -- state -------------------------------------------------------------- #

    def _arrived(self) -> bool:
        return (
            self.mail_at_s is not None
            and self.clock.now - self.created_at >= self.mail_at_s
        )

    def _listed(self) -> bool:
        if not self._arrived():
            return False
        return self.refreshes > 0 if self.needs_refresh else True

    def _message_open(self) -> bool:
        return (
            self.opened_at is not None
            and self.clock.now - self.opened_at >= self.open_delay_s
        )

    def _message_text(self) -> str:
        if not self._message_open():
            return ""
        lines = ["Replit", "Verify your email address"]
        if self.verify_control == "verify_now":
            lines.append("Verify Now")
        elif self.verify_control == "verify_email":
            lines.append("Verify Email")
            if self.now_revealed:
                lines.append("Verify Now")
        if self.text_url:
            lines.append(f"Or copy this link: {self.text_href}")
        return "\n".join(lines)

    def _message_html(self) -> str:
        if not self._message_open():
            return ""
        parts = []
        if self.verify_control != "none":
            parts.append(f'<a href="{self.href}" target="_blank">Verify</a>')
        if self.text_url:
            parts.append(f"<p>{self.text_href}</p>")
        return "".join(parts)

    def _main_text(self) -> str:
        lines = ["Temp Mail", "Your Temporary Email Address", self.shown_address, "Inbox"]
        if self._listed():
            lines.append("verify@replit.com\nReplit: Verify Your Email")
        if self.message_view == "inline" and self._message_open():
            lines.append(self._message_text())
        return "\n".join(lines)

    def _main_html(self) -> str:
        if self.message_view == "inline":
            return self._message_html()
        return ""

    # -- behaviour ---------------------------------------------------------- #

    def _on_refresh(self) -> None:
        self.refreshes += 1

    def _open_message(self) -> None:
        if self.opened_at is None:
            self.opened_at = self.clock.now

    def _on_row_click(self) -> None:
        if self.row_opens:
            self._open_message()

    def _on_subject_click(self) -> None:
        if self.subject_opens:
            self._open_message()

    def _open_link(self) -> None:
        self.context.open_verification_tab(self.href)

    def _on_legacy_click(self) -> None:
        if self.legacy_first_click in {"starts", "starts_and_reveals"}:
            self._open_link()
        if self.legacy_first_click in {"reveals_now", "starts_and_reveals"}:
            self.now_revealed = True


class FakeReplitContext:
    """A browser context holding one fake Replit tab and its login cookies."""

    def __init__(self, page, *, clearing_works: bool = True) -> None:
        self.page = page
        self.pages = [page]
        self.clearing_works = clearing_works
        self.cleared: list[dict] = []

    def new_page(self):
        return self.page

    def clear_cookies(self, **kwargs: Any) -> None:
        self.cleared.append(kwargs)
        if self.clearing_works:
            self.page.logged_in = False
