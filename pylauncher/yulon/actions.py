"""One answer shape, shared by every feature that presses a command (8.3a, 8.4a).

`Outcome` and the mapping that builds it were `useraccounts`'s, and 8.4a needs
exactly the same three shapes for the same reason -- so they live here rather
than one feature reaching into another's internals for a private name.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass
from typing import Any, TypeVar

from yulon.said import SaidByYulon


@dataclass(frozen=True)
class Outcome:
    """What happened, in the three shapes an answer can take.

    `done` false with an empty `problem` never happens; `done` false and a
    problem naming an unreachable server is NOT the same as one naming a
    refusal, and the tab says which. Reporting "could not ask" as "did not
    work" would have the user believe a password is unchanged when nobody
    knows whether it is.
    """

    done: bool
    text: str = ""
    problem: str = ""
    indeterminate: bool = False
    """The command may have run, and nobody knows whether it did.

    A SOAP timeout is not a failure: the listener queues onto the world thread
    and blocks until the command finishes, so a client giving up says nothing
    about whether the server did. Reported as a plain failure, a person retypes
    the old password and is locked out of an account whose password has already
    changed (adversarial review, 2026-09-07).
    """


def send(channel: object, line: str) -> Outcome:
    return outcome_of(channel.send(line))  # type: ignore[attr-defined]


def outcome_of(answer: object) -> Outcome:
    """One channel answer, as the three shapes above.

    Apart from `send()` for a caller that has to read the answer's own outcome
    first (T301: a refused delete is said in its own words).
    """
    outcome = getattr(answer, "outcome", "")
    if outcome == "yes":
        return Outcome(True, text=getattr(answer, "text", ""))
    if outcome == "no":
        return Outcome(False, problem=getattr(answer, "text", "the server refused"))
    reason = getattr(answer, "reason", "") or "the server could not be asked"
    if getattr(answer, "indeterminate", False):
        # No mechanism here. Two different machines arrive at this branch -- a
        # timeout, where this app gave up while the server worked on, and a
        # CMaNGOS refusal, where the server hung up on us at once (8.3b) -- and
        # a sentence that names one of them describes something that did not
        # happen for the other. The channel's own reason already says which.
        return Outcome(
            False,
            indeterminate=True,
            problem=(
                f"{reason.rstrip('.')}. The change may already have been made, so check "
                "before trying it again."
            ),
        )
    return Outcome(False, problem=reason)


ServerHold = Callable[[str], AbstractContextManager[None]]
"""The seam that reserves a server across processes for a block, named by the press (T607)."""

_M = TypeVar("_M", bound=Callable[..., Outcome])


def holding(press: str) -> Callable[[_M], _M]:
    """Run a write method of a class with `self._hold_server` under that server hold (T610).

    The method is run inside the server's cross-process reservation under `press`. When another
    Yu'lon holds the server, nothing runs and the answer is a refusal `Outcome` that says the
    holder's own sentence, so a tab shows it like any other refusal. A class built with no hold
    (a harness, a test) runs the method as before. The decorator marks the function with
    `server_hold_press`, which is what the guard test over every public method looks for: a new
    write method of such a class that forgets it fails there.
    """

    def decorate(method: _M) -> _M:
        @functools.wraps(method)
        def held(self: Any, *args: Any, **kwargs: Any) -> Outcome:
            hold: ServerHold | None = getattr(self, "_hold_server", None)
            if hold is None:
                return method(self, *args, **kwargs)
            with ExitStack() as reserved:
                try:
                    reserved.enter_context(hold(press))
                except SaidByYulon as refused:
                    return Outcome(False, problem=str(refused))
                return method(self, *args, **kwargs)

        held.server_hold_press = press  # type: ignore[attr-defined]
        return held  # type: ignore[return-value]

    return decorate
