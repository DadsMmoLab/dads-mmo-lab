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
type that carries it is shown after a Stop, and everything else is the Stop.
"""

from __future__ import annotations


class TrueAfterStop(Exception):
    """Mixed into an exception type whose message says what a press LEFT, Stop or no Stop.

    Mixed in after the type's own base -- `class RollbackNotDone(InstallerError,
    TrueAfterStop)` -- so every `except InstallerError` already written still
    catches it. `LogPanel` reads it (`_StreamWorker.run()`): a job that raises one
    after Stop was pressed ends with the sentence, under "Stopped".
    """
