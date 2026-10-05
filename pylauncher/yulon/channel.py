"""One command in, one typed answer out (Phase 8.2a).

The seam every feature above this calls. It holds no command text — that is
`commands.py` — and no UI. What it owns is three things:

**The three-outcome answer.** `yes` is a command the server ran and reported as
succeeding. `no` is one it ran and refused. `unknown` is everything else, and it
is not a failure of the command: it is the absence of an answer about it. The
spike that measured a published port against a listener on the container's own
loopback found exactly these three on the wire — refused, silent, answering —
so the type is the shape the transport already has.

**A write is never retried.** A timeout carries `indeterminate=True` and stops
there. The command may have run: the listener queues onto the world thread and
blocks until it finishes (`ACSoap.cpp:120-130`), so a client giving up says
nothing about whether the server did. A retry that then succeeded would report
"done" for a command that ran twice — two accounts, two mails, two of whatever
it was.

**One lock per install.** The SOAP listener runs on the single world thread, so
two commands in flight are two things queued behind each other at best. The
prior art serialises for the same reason and says so
(`launcher/src-tauri/src/lib.rs:117-123`), noting that even the bash CLI took a
`~/.dml/soap.lock` file lock. Per install rather than global, because this app
can hold two servers open and they are two world threads.

And when it could not ask, it says **why** — read from container state at that
moment rather than guessed, so the sentence tells the user what to do next.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from yulon import docker, soap
from yulon.log import get_logger

logger = get_logger(__name__)

Outcome = Literal["yes", "no", "unknown"]


@dataclass(frozen=True)
class Answer:
    """What one command came back as."""

    outcome: Outcome
    text: str = ""
    reason: str = ""
    """Why there is no answer, when there is none. Empty for `yes` and `no`."""
    indeterminate: bool = False
    """True when the command may have run, so a caller must not simply retry.

    A SOAP timeout sets it: the request was sent and the answer never came. So
    does an attach console whose reply window held no prompt -- the command was
    typed and nothing came back delimited (8.2e). A console that could not be
    reached at all does NOT set it, because nothing was typed.
    """
    denied: bool = False
    """True only when the SERVER said it does not accept this credential.

    Every transport failure and every rejection arrives as `unknown`, so a
    caller reading the outcome alone cannot tell "your password is wrong" from
    "the server is not there". 8.2a's repair rotates a GM account's password on
    the strength of that distinction, and an adversarial review found what
    happens without it: opening the tab against a stopped server read as a bad
    credential and offered to reset a working one.

    `forbidden` is deliberately NOT denied. The account exists and its password
    is right; what is wrong is its GM level, and a new password would burn a
    rotation and change nothing.
    """

    @property
    def known(self) -> bool:
        return self.outcome in ("yes", "no")


class Channel(Protocol):
    """What a feature module is handed. Deliberately one method."""

    def send(self, command: str) -> Answer: ...


class AttachChannel:
    """The attach-console transport, for a core with no SOAP and no RA (8.2e).

    Written for Tortoise when its mangosd linked neither gsoap nor `RASocket`
    (measured 2026-09-07 at the pin of that day, `7c0fb278`: its source and
    dependency lists named neither). The fork re-added SOAP on 2026-09-07
    (`3f9a062`) and the pin moved onto it on 2026-09-08 (`3a8472e`), so that
    tree now has a listener too and its entry says `soap`; this transport stays
    for the console the Console tab types at, live-gated against a real
    worldserver on 2026-08-23, and for any core that ships no listener. It wraps
    the console in the one method every feature module is handed.

    **The distinction this class exists for** is what a window with no prompt in
    it means. `ConsoleReply.prompted` is False when nothing was delimited, and
    the transport cannot tell why: `docker attach` failing before it reached a
    console looks exactly like a worldserver that is up and still loading its
    maps. Either way the command may have been typed and may have run. So that
    is `unknown` carrying `indeterminate` -- "I could not ask" -- and never
    `no`, which would invite a caller to do it again.

    A `ConsoleError` is the opposite half and is deliberately NOT indeterminate:
    it is raised before anything is typed, so the command certainly did not run.

    Nothing here is ever `denied`. There is no credential on this transport to
    reject, and a feature keying off that flag must not read "no prompt" as "your
    password is wrong".
    """

    def __init__(
        self,
        *,
        send: Callable[..., object],
        window: float | None = None,
    ) -> None:
        self._send = send
        self._window = window
        self.lock = threading.Lock()
        """This install's lock, as `SoapChannel` has: one console, one typist."""

    def send(self, command: str) -> Answer:
        """Type one command at the console and read that command's answer."""
        with self.lock:
            try:
                reply = (
                    self._send(command, window=self._window)
                    if self._window is not None
                    else self._send(command)
                )
            except Exception as exc:  # noqa: BLE001 - every console failure is an answer here
                logger.info(f"the console could not be reached: {exc}")
                return Answer(
                    outcome="unknown",
                    reason=str(exc),
                    # Nothing was typed, so nothing may have run. Saying it
                    # might have is as wrong as saying it failed.
                    indeterminate=False,
                )
        lines = tuple(getattr(reply, "lines", ()))
        text = "\n".join(lines)
        if not getattr(reply, "prompted", False):
            return Answer(
                outcome="unknown",
                text=text,
                reason=(
                    # What happened, and only that. What it IMPLIES -- that the
                    # command may still have run -- is `indeterminate`, and the
                    # surface that shows this to a person says it in its own
                    # words. Saying it here too printed it twice on the tab
                    # (measured on m910q, 8.2e's gate).
                    "the console printed no prompt inside the reply window, so nothing in it is "
                    "this command's answer"
                ),
                indeterminate=True,
            )
        return Answer(outcome="yes", text=text)


class SoapChannel:
    """The SOAP transport, for every tree whose entry says `soap`.

    Until 2026-09-08 that was WotLK, TBC and Vanilla, and Tortoise reached its
    world through `AttachChannel` (8.2e) because its mangosd had no SOAP. The
    fork re-added the interface (`3f9a062`) and the pin moved onto it
    (`3a8472e`), measured on the fresh install on yulon-arch.
    """

    def __init__(
        self,
        *,
        endpoint: soap.Endpoint,
        state_of: Callable[[], docker.ContainerState],
        send: Callable[..., soap.Reply] = soap.execute,
        timeout: float = soap.DEFAULT_TIMEOUT,
    ) -> None:
        self.endpoint = endpoint
        self._state_of = state_of
        self._send = send
        self._timeout = timeout
        self.lock = threading.Lock()
        """This install's lock. One per channel, so two servers do not queue."""

    def send(self, command: str) -> Answer:
        """Ask once, hold the lock only for the asking, and never retry."""
        with self.lock:
            reply = self._send(self.endpoint, command, timeout=self._timeout)
        return self._answer(reply)

    def _answer(self, reply: soap.Reply) -> Answer:
        if reply.outcome == "answered":
            return Answer("yes", reply.text)
        if reply.outcome == "refused":
            return Answer("no", reply.text)
        if reply.outcome == "unauthorised":
            return Answer(
                "unknown",
                reply.text,
                denied=True,
                reason=(
                    "the server did not accept this install's account and password. Its "
                    "credentials may have been changed on the server, or the file this app "
                    "keeps them in may be stale."
                ),
            )
        if reply.outcome == "forbidden":
            return Answer(
                "unknown",
                reply.text,
                reason=(
                    "this install's account exists but its GM level is below administrator, "
                    "which is the level the command channel needs."
                ),
            )
        if reply.outcome == "silent":
            # The connection is better evidence than anything docker could say
            # about the container: this app just completed a TCP connection to
            # the command channel and put a whole request into it. So no
            # `_why`, which would answer "this server is not running" for a
            # machine it had spoken to a millisecond earlier.
            return Answer(
                "unknown",
                reply.text,
                reason=(
                    "the server took the command and closed the connection without answering. "
                    "On this server that is also what a refused command looks like, so the "
                    "command may have run."
                ),
                indeterminate=True,
            )
        if reply.outcome == "unreadable":
            # A whole HTTP reply came back, so something IS listening, and the
            # container state has nothing better to say (T226). It carried
            # neither a result nor a fault, so whether the command ran is not
            # known: indeterminate, never a channel that is off.
            return Answer(
                "unknown",
                reply.text,
                reason=(
                    "the server answered, but its reply could not be read, so whether the "
                    "command ran is not known"
                ),
                indeterminate=True,
            )
        return Answer(
            "unknown",
            reply.text,
            reason=self._why(reply.outcome),
            indeterminate=reply.outcome == "timeout",
        )

    def _why(self, outcome: str) -> str:
        """Turn a transport failure into the reason the container state gives.

        Asked at the moment it failed rather than remembered, because the whole
        value of the sentence is that it describes now.
        """
        state = self._state_of()
        if state.status == "":
            return "docker could not be asked about this server, so nothing is known about it"
        if state.status == "restarting":
            return (
                f"this server is in a restart loop ({state.restart_count} restarts), so nothing "
                "is listening for long enough to answer"
            )
        if state.status != "running":
            return "this server is not running, so there is nothing to answer"
        if outcome == "timeout":
            # The spike's finding: a running, settled world that accepts and
            # says nothing is a bind nobody can reach, not a slow command.
            return (
                "the server is running but the command channel did not answer. Either it is "
                "still loading, or the listener is bound inside the container where the "
                "published port cannot reach it."
            )
        return (
            "the server is running but nothing is listening on the command channel — it may not "
            "be turned on for this install yet"
        )
