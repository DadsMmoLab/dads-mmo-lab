"""The mark on a failure whose text Yu'lon wrote for the player (T214).

A failure the player is shown is one of two kinds. Yu'lon's own sentence -- a
refusal that says what was not done and what to do -- goes on the line as
written. Anything else is something that broke: a program's own output, the
system's, a bug's. The line says so in plain words and the text goes under
Details and to the log.

The mark is positive on purpose. The first rule read the text instead ("one of
four types, and no `… exited <code>:` in it"), and every refusal type also
carries raw output in some other shape: mysql's `ERROR 1146 (42S02) …` through
`apply._check_sql`, `docker exec`'s `OCI runtime exec failed …`. Those reached
the line. Here nothing is Yu'lon's unless the code that raised it says so.
"""

from __future__ import annotations

from typing import TypeVar


class SaidByYulon(Exception):
    """Mixed into an exception type whose message is a sentence Yu'lon wrote for the player.

    Mixed in after the type's own base -- `class ApplyRefusal(ApplyError, SaidByYulon)` --
    so every `except ApplyError` already written still catches it. A type whose
    every instance is Yu'lon's (`StartRefused`) carries it on the class; a type
    that holds both kinds (`DockerCommandError`) has a refusal subclass, raised
    where the sentence is Yu'lon's.

    `detail` is a program's own words that explain the sentence -- an
    importer's last lines -- kept OUT of the message, so the line says what
    happened and Details shows what the program said.
    """

    detail: str = ""


DETAILS_HEADING = "\n\nDetails:\n"
"""Where `with_details()` puts a text's Details, and where `split_details()` cuts it."""


def with_details(exc: BaseException, sentence: str | None = None) -> str:
    """`sentence` (else `exc`'s own) with its Details under it, for a place with no pane (T248).

    Any exception's `detail` counts: `SaidByYulon`'s and `InstallerError`'s. The
    install's failure dialog has no Details to fold away, so what a program
    printed -- and a command, when one helps -- goes under a "Details:" heading
    in the text itself, below the sentence and never in it.
    """
    said = str(exc) if sentence is None else sentence
    detail = getattr(exc, "detail", "")
    return details_below(said, detail if isinstance(detail, str) else "")


def details_below(sentence: str, detail: str) -> str:
    """`sentence`, and `detail` under a "Details:" heading when there is any (T248).

    For a failure whose text is all a place gets (`InstallerError`): the line
    shows the sentence (`split_details()`), the dialog shows both.
    """
    return f"{sentence}{DETAILS_HEADING}{detail}" if detail else sentence


def split_details(text: str) -> tuple[str, str]:
    """`with_details()` undone: the sentence for a line, and the Details for a fold (T248)."""
    line, _, details = text.partition(DETAILS_HEADING)
    return line, details


_E = TypeVar("_E", bound=BaseException)


def carry_detail(cause: BaseException, error: _E) -> _E:
    """`error`, holding `cause`'s Details unless it has its own (T248)."""
    if not getattr(error, "detail", ""):
        detail = getattr(cause, "detail", "")
        if detail:
            error.detail = detail  # type: ignore[attr-defined]
    return error
