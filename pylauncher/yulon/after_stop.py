"""The mark on a failure whose sentence is still true, and still needed, after a Stop (T228).

A job the player stopped usually ends by raising: the Stop ended a child, the
child exited non-zero, and the route raised on the way out. That failure is
the Stop taking effect, and the log panel says "cancelled" for it rather than
putting a refusal on screen for a button the player pressed.

Some failures are not that. A rollback that stopped before the old build was
back, sources left on the new commits, servers left stopped with a start
refused until something is done: those sentences say what state the server is
in and what to press, and a Stop does not make them any less true. They were
dropped with the rest, so the panel said "cancelled" over a server it had left
in a state nobody was told about (m910q, 2026-10-04, P9).

The mark is positive and on the type, so the panel never reads the words: a
type that carries it is shown after a Stop.

The other side has its own mark since T250. What IS the Stop taking effect --
a child the Stop ended, a stage that heard the cancel -- carries
`StopTookEffect`, and only that is "cancelled"; any other failure after a Stop
is shown too, because it is a real failure that landed as the button was
pressed, not the press.
"""

from __future__ import annotations

import threading


class TrueAfterStop(Exception):
    """Mixed into an exception type whose message says what a press LEFT, Stop or no Stop.

    Mixed in after the type's own base -- `class RollbackNotDone(InstallerError,
    TrueAfterStop)` -- so every `except InstallerError` already written still
    catches it. `LogPanel` reads it (`_StreamWorker.run()`): a job that raises one
    after Stop was pressed ends with the sentence, under "Stopped".
    """


class StopTookEffect(Exception):
    """Mixed into an exception type that IS the player's Stop taking effect (T250).

    The other half of `TrueAfterStop`, and the one that decides "cancelled".
    Until T250 the log panel read every exception that arrived after Stop as
    the Stop, by timing alone, so a real failure that landed in the same
    moment -- a full disk, a lost daemon, a crash loop seen as the press came
    -- was shown as a clean cancel. Now a stopped job is a clean cancel only
    when what ended it says so by type: this mark, on the exception or on one
    it was raised `from` (`stop_took_effect()`). Anything else is a failure,
    shown under "Stopped".

    Mixed in after the type's own base, as `TrueAfterStop` is, so every
    `except` already written still catches it.
    """


class StopSaid(StopTookEffect):
    """A Stop taking effect whose sentence is for the player: shown as "Stopped: <it>" (T528).

    The engine's own `InstallStopped` says what the Stop cost at the stage it
    landed in -- "the build was stopped." and the note of what is kept -- and the
    panel used to drop it for a bare "cancelled" (seen live 2026-10-07 on
    yulon-ubuntu2 and yulon-win11). A Stop that only names what it ended (a
    stopped git command, a child's exit) is a plain `StopTookEffect` and stays
    "cancelled". Only the exception's own type counts, not one it was raised
    `from`: a route that words the Stop again decides what is said.
    """


def stop_took_effect(exc: BaseException) -> bool:
    """Is `exc` the Stop taking effect: marked `StopTookEffect`, or raised `from` one that is?

    `__cause__` only, never `__context__`. A route that turns a stopped child
    into its own sentence raises `from` it (ruff's B904 holds every handler in
    this tree to that); a cleanup that FAILED while a Stop was being handled
    carries the Stop only as its context, and that failure is the cleanup's
    own, so it is shown.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, StopTookEffect):
            return True
        seen.add(id(current))
        current = current.__cause__
    return False


def withdraw_stop(cancel: threading.Event | None) -> bool:
    """The job's Stop came too late to change anything: take it back, so the press SUCCEEDS.

    The lead's ruling (T247 review, 2026-10-05): a rebuild whose new world had
    already passed its whole watch when Stop was pressed has met its proof, so
    the build is kept, and the press is a success -- not a cancel. The job
    clears its Cancel (and the "Stop now anyway" riding on it), which does two
    things, and both are the point: what the press still runs (an update's
    after-work, Tortoise's dashboard rebuild) sees no Stop; and `LogPanel`,
    finding the Cancel it set cleared when the job ends, reports the job as
    finished, so every owner's success path runs. A Stop pressed again after
    this sets the Cancel again and is an ordinary Stop.

    **Only a Stop is taken back (T607).** A press whose server reservation was lost from
    elsewhere (another Yu'lon's "Stop anyway", Docker restarting) has its cancel set by that
    loss, and the server is no longer what the press proved: that is not a Stop that came too
    late. The cancel carries the loss as `reservation_lost`, and while it is set nothing is
    cleared. Returns whether the Stop was taken back (False: nothing to take back, or a loss).
    """
    if cancel is None:
        return False
    lost = getattr(cancel, "reservation_lost", None)
    if isinstance(lost, threading.Event) and lost.is_set():
        return False
    anyway = getattr(cancel, "anyway", None)
    if isinstance(anyway, threading.Event):
        anyway.clear()
    cancel.clear()
    return True


class PutBackAfterStop(TrueAfterStop):
    """A stopped press that put EVERYTHING back cleanly: shown as "Stopped:", not FAILED.

    The lead's ruling (T247 live review, 2026-10-05): a Stop during a rebuild's
    load that ends in a clean rollback -- the build from before up again, and
    with T217 its databases put back too -- left nothing wrong, so the header is
    a plain stop whose sentence says what was put back. A `TrueAfterStop`,
    because the sentence is still the one the player needs; anything that left
    something wrong stays a plain `TrueAfterStop`, under "Stopped. FAILED".
    """
