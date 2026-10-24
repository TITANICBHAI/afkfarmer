"""Work out which step the run is really in, from what the browser and phone show.

Pure functions: no browser, no ADB. The caller gathers the observations (see
``ReplitAutomationOrchestrator.detect_position``) and this module decides.

The saved checkpoint (``state.json``) is only a hint. Evidence found on screen
can move the run FORWARD past work that is visibly already done (the person
finished a step by hand, or the previous run died right after it). It never
moves the run backwards on its own: repeating a finished step, for example
signing up the same address twice, would do more harm than skipping it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

STAGES = ("EMAIL", "SIGNUP", "VERIFY", "ANDROID", "PC_LOGIN", "GITHUB", "DONE")

# Phone screens that can only be reached when the app is already logged in.
PHONE_PAST_LOGIN = frozenset(
    {
        "welcome_onboarding", "name", "username", "source", "role", "plan",
        "notifications_prompt", "main", "profile_menu", "logout_dialog",
    }
)
PHONE_LOGIN = frozenset(
    {
        "initial_login", "email_choice", "email_login", "password_login",
        "login_processing", "invalid_credentials",
    }
)


@dataclass(frozen=True)
class Position:
    """Where the run is: the step, the point inside that step, and why we think so."""

    stage: str
    substage: str
    reason: str
    moved: bool = False  # True when the evidence overrode the saved checkpoint

    def describe(self) -> str:
        return f"{self.stage} / {self.substage} - {self.reason}"


def _index(stage: str) -> int:
    return STAGES.index(stage) if stage in STAGES else 0


def infer_position(
    saved_stage: str,
    state: dict,
    web_kind: Optional[str] = None,
    phone_state: Optional[str] = None,
) -> Position:
    """Combine the checkpoint with what is on screen.

    ``web_kind`` is ``pc_flow.classify_replit_page`` of the open Replit tab (or
    None when no browser was reachable); ``phone_state`` is
    ``AndroidAutomation.observe_screen`` (None when it was not probed).
    """

    saved = saved_stage if saved_stage in STAGES else "EMAIL"
    has_email = bool(state.get("temp_email"))
    substage = _substage(saved, web_kind, phone_state)

    if saved != "EMAIL" and saved != "DONE" and not has_email:
        return Position(
            "EMAIL",
            "no address saved",
            f"the checkpoint says {saved}, but no e-mail address is saved to continue with",
            moved=True,
        )

    best = Position(saved, substage, "saved checkpoint")

    if phone_state in PHONE_PAST_LOGIN and has_email and _index(saved) < _index("ANDROID"):
        best = Position(
            "ANDROID",
            f"phone shows '{phone_state}'",
            "the Replit app on the phone is already logged in, so sign-up and "
            "verification are done",
            moved=True,
        )
    elif web_kind == "check_email" and has_email and _index(saved) < _index("VERIFY"):
        best = Position(
            "VERIFY",
            "waiting for the verification message",
            "the browser shows Replit's 'check your email' page, so sign-up is done",
            moved=True,
        )
    return best


def _substage(stage: str, web_kind: Optional[str], phone_state: Optional[str]) -> str:
    """The most specific thing known about where inside ``stage`` the run is."""

    if stage == "ANDROID":
        if phone_state in {"no_device", "not_replit", None, "unknown"}:
            return "app not in front" if phone_state == "not_replit" else "not observed"
        if phone_state in PHONE_LOGIN:
            return f"login ({phone_state})"
        return f"onboarding ({phone_state})"
    if stage in {"SIGNUP", "VERIFY", "PC_LOGIN", "GITHUB"}:
        return web_kind or "not observed"
    return "start"
