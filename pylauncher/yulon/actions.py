"""One answer shape, shared by every feature that presses a command (8.3a, 8.4a).

`Outcome` and the mapping that builds it were `useraccounts`'s, and 8.4a needs
exactly the same three shapes for the same reason -- so they live here rather
than one feature reaching into another's internals for a private name.
"""

from __future__ import annotations

from dataclasses import dataclass


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
