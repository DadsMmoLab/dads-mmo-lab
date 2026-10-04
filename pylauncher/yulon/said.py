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


class SaidByYulon(Exception):
    """Mixed into an exception type whose message is a sentence Yu'lon wrote for the player.

    Mixed in after the type's own base -- `class ApplyRefusal(ApplyError, SaidByYulon)` --
    so every `except ApplyError` already written still catches it. A type whose
    every instance is Yu'lon's (`StartRefused`) carries it on the class; a type
    that holds both kinds (`DockerCommandError`) has a refusal subclass, raised
    where the sentence is Yu'lon's.
    """
