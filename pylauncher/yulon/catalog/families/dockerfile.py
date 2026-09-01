"""Render a game's `Dockerfile` and `.dockerignore` from templates, under the marker rule.

The rule is `composegen.GENERATED_MARKER`'s, reused rather than restated: a file that
begins with the marker is ours to rewrite, a file that does not is refused untouched.
The incident behind it (`rust-prior-art.md` §1) was a generator overwriting a real
server's compose file; a Dockerfile is the same shape of file in the same folder, so it
gets the same rule and the same first-line test.

Three answers, not two. "May we overwrite this?" has a third one — *we could not tell* —
and `composegen.is_ours()` folds it into "no", which the caller then reports as "that
file was not written by Yu'lon. Point the install at an empty folder, or move that file
aside." Said about a file nobody could open, that is an accusation the evidence does not
support and a remedy that does not fix a permission problem. `_look()` keeps the three
apart and reports what the OS actually said.

The marker constant, the first-line test and `fill()` all still come from `composegen`:
one mechanism, one spelling. What this module adds is the third answer and a single
read — `_look()` opens each file once and answers both questions from that one read
("may we write" and "is the text already right"). Asking twice is how a transient read
failure becomes a fabricated accusation about the user's folder.

This module renders and writes. It never runs a build — the spine's `build` stage does —
and it holds no template text: that is
`catalog/installers/<game>/native/{Dockerfile.tmpl,dockerignore.tmpl}` (style-guide §3).
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import fields
from pathlib import Path
from typing import Any

from yulon.catalog import composegen
from yulon.catalog.native import Secrets
from yulon.log import get_logger

logger = get_logger(__name__)

DOCKERFILE = "Dockerfile"
DOCKERIGNORE = ".dockerignore"
TEMPLATE = "Dockerfile.tmpl"
IGNORE_TEMPLATE = "dockerignore.tmpl"


def secret_tokens(secret_type: type[Any]) -> frozenset[str]:
    """The `{{TOKEN}}` names a secret dataclass's fields stand for: `db_password` is `DB_PASSWORD`.

    Derived from the declaration rather than listed here, because a list is a
    thing somebody has to remember to extend. `native.Secrets` is where this app
    declares what may never be printed, and `CmangosInstaller._tokens()` spells
    each of its fields as the upper-cased token of the same name into contract
    A6's one mapping — so reading the dataclass IS reading the declaration, and a
    second secret added there arrives here already refused.

    Measured on the unfixed code, 2026-09-01 (commit b8973c52): with this
    refusal written as one hard-coded name, a template spelling any OTHER
    secret-bearing token rendered its value straight into the Dockerfile —
    `ENV SOAP_PASSWORD=tbc-0123456789abcdef` came back from `render()` — and the
    whole suite stayed green.
    """
    return frozenset(field.name.upper() for field in fields(secret_type))


SECRET_TOKENS = secret_tokens(Secrets)
"""Every token in the installer's mapping that may not reach a file this module writes.

`composegen.generate()` took this decision for the compose files and refuses `DB_PASSWORD`
by name; the same decision, for a stronger reason, applies here. A secret in a compose
file is in a file the user owns — delete it, rotate, done. A secret in a Dockerfile is
copied into a content-addressed image LAYER, so `docker history` prints it long after the
Dockerfile is gone and undoing it means finding and deleting every image built from that
layer. Compose is "delete and rotate"; a Dockerfile is "you now have an artefact to hunt".

The caller hands `render()` one mapping for the Dockerfile, the conf tables, the SQL and
verify alike (contract A6), and that mapping carries the password because the conf tables
need it (they are written 0600; this file is written 0644). Which is why the refusal lives
here, in the renderer, rather than in a test over the shipped templates: a test protects a
LOCATION — and one was defeated by planting a template in `shared/cmangos/`, a folder its
glob never walked; the glob was widened to the whole installers tree, and
`install_wiring.py`'s `--installers-root` then points the ENGINE at a different tree
entirely, which that glob does not walk either — whereas the refusal protects the
property, for any template dir any `dockerfile_dir` ever names.

A SET, and a derived one, because the by-name version protected one NAME rather than the
property: `Secrets` is a dataclass and a second field on it is one line away.
"""


class DockerfileError(RuntimeError):
    """A template could not be rendered, or a file in the way is not ours to replace."""


def render(template_dir: Path, tokens: Mapping[str, str]) -> tuple[str, str]:
    """(Dockerfile text, .dockerignore text), each beginning with the marker as a `#` comment.

    One `fill()` for every template in the app — `composegen.fill`, which refuses an
    unfilled `{{TOKEN}}` — so a Dockerfile cannot ship a literal placeholder that `docker
    build` would happily read as a path. Unused tokens are fine, and the caller relies on
    that: `CmangosInstaller._write_dockerfile` passes the whole of
    `CmangosInstaller._tokens(ctx)` — `entry_tokens(entry)` plus the per-install keys
    (`DB_PASSWORD`, `REALM_HOST`, the three ports, `PROJECT_NAME`, `IMAGE_PREFIX`,
    `IMAGE_TAG`) — while the two templates spend only `CORE_DIR` and `MAKE_JOBS`. The
    mapping that arrives here therefore CARRIES the secret, deliberately (contract A6),
    and keeping it out of both rendered files is this function's own job, below.

    The text comes back LF whatever the worktree holds. The shipped templates are LF in
    git's index and CRLF in a Windows checkout under `core.autocrlf=true`; reading them
    in text mode translates that away, and `write()` is what keeps it translated away.

    Every token in `SECRET_TOKENS` is dropped from the mapping before either half is
    filled — belt to the refusal's braces, exactly as `composegen.generate()` does it. A
    template that spelled one is refused by name below; were that refusal ever removed,
    the token would then be UNFILLED rather than quietly rendering the secret into the
    build context.

    Both halves come back as `_Rendered`, which is the only text `write()` will lay down.

    Raises:
        DockerfileError: a template could not be read, a placeholder was left unfilled,
            or a template names a `SECRET_TOKENS` placeholder such as `{{DB_PASSWORD}}`.
    """
    safe = {key: value for key, value in tokens.items() if key not in SECRET_TOKENS}
    return (
        _Rendered(_render_one(template_dir / TEMPLATE, safe)),
        _Rendered(_render_one(template_dir / IGNORE_TEMPLATE, safe)),
    )


class _Rendered(str):
    """Text that came out of THIS module's `render()`, and the only text `write()` lays down.

    A `str` subclass rather than a wrapper so no call site changes —
    `CmangosInstaller._write_dockerfile` unpacks the pair and hands both halves straight
    to `write()` — and so that every string operation on it (`.replace`, slicing, `+`,
    `.strip`) yields a plain `str`. Text edited after it was rendered is not the text
    `render()` checked, and `write()` treats it as unrendered, which is the intent.

    Measured 2026-09-01, the exceptions to "every operation drops the subclass":
    `copy.copy`, `copy.deepcopy` and a `pickle` round trip all PRESERVE `_Rendered`, since
    a `str` subclass reconstructs through `cls.__new__`. Not a hole, since the text is
    unchanged and `render()` already judged it, but a reader applying the blanket rule to
    those three would be wrong.
    """

    __slots__ = ()


def _render_one(path: Path, tokens: Mapping[str, str]) -> str:
    """Fill one template and guarantee the marker — without stacking a second one.

    The shipped templates open with the marker themselves, because the sentence that
    tells a user not to hand-edit the file belongs with the text it is about. Prepending
    unconditionally would put two marker lines at the top of every generated Dockerfile.
    The promise is that the marker is THERE, so it is added only when it is missing.
    """
    try:
        template = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DockerfileError(f"the template {path} could not be read: {exc}") from exc
    for token in sorted(SECRET_TOKENS):
        if "{{" + token + "}}" in template:
            raise DockerfileError(
                f"{{{{{token}}}}} appears in {path}, but a generated file in the build "
                "context is not where a secret may go: a Dockerfile is copied into "
                "an image layer, and `docker history` prints that layer long after the file "
                "is deleted. The install writes secrets into the 0600 `.conf` files at run "
                "time; keep them out of the image."
            )
    try:
        filled = composegen.fill(template, tokens)
    except composegen.ComposeGenError as exc:
        raise DockerfileError(f"{path}: {exc}") from exc
    if filled.startswith(composegen.GENERATED_MARKER):
        return filled
    return f"{composegen.GENERATED_MARKER}\n{filled}"


class _Verdict(enum.Enum):
    """What one read of a path in the server dir proved about it."""

    OURS = "ours"
    """Missing, or its first line carries the marker: this engine may rewrite it."""

    THEIRS = "theirs"
    """Read, and it carries no marker. Somebody else's file; the install stops."""

    UNREADABLE = "unreadable"
    """Could not be opened, so nothing was proved either way. Also stops the install,
    but says so — the remedy for "I cannot read this" is not "point somewhere else"."""


def _look(path: Path) -> tuple[_Verdict, str | None, OSError | None]:
    """Open `path` ONCE: the verdict, the exact text on disk, and the error if any.

    The text comes back untranslated (`newline=""`), which is what makes the "unchanged,
    leave it alone" test in `write()` a byte comparison. Through `read_text()` a CRLF
    copy of our own text compares EQUAL to the LF text we meant to write, so the skip
    would preserve a CRLF Dockerfile forever.

    Undecodable bytes are replaced rather than raising, as in `composegen.is_ours()`: a
    binary file in the way has no marker on its first line, which is the honest answer.
    """
    try:
        with path.open(encoding="utf-8", errors="replace", newline="") as handle:
            text = handle.read()
    except FileNotFoundError:
        return (_Verdict.OURS, None, None)
    except OSError as exc:
        logger.warning(f"could not read {path} to see whether Yu'lon wrote it: {exc}")
        return (_Verdict.UNREADABLE, None, exc)
    if text.startswith(composegen.GENERATED_MARKER):
        return (_Verdict.OURS, text, None)
    return (_Verdict.THEIRS, text, None)


def write(server_dir: Path, dockerfile: str, dockerignore: str) -> tuple[Path, ...]:
    """Lay both files in `server_dir`; the paths actually written come back.

    Unchanged text is left alone so the file's mtime does not move and a resume's
    evidence ("marker + text equal") stays cheap. Three things must hold before anything
    is laid down: the text carries the marker, the text is `render()`'s own output, and
    the file already on disk carries the marker. Nothing is written until BOTH files have
    been judged, so a refusal leaves the folder exactly as it was — a Dockerfile written
    before the `.dockerignore` was refused would be skipped as "unchanged" on the retry,
    and the pair could never be proved consistent.

    **Why provenance and not a content re-check.** This is a public entry point around
    `render()`, and it used to validate only the marker it had generated itself: measured
    2026-09-01 on commit b8973c52, marked text reading `ENV PW=<the password>` was written
    into the build context without a word. Re-checking the CONTENT here was the
    alternative and it is weaker in both directions, because `write()` is never given the
    secret VALUES — they live in `native.Secrets`, which this module's signature never
    sees — so a re-check could only scan for the token NAMES: it would miss a password
    stored under any other name (exactly the text above) and refuse a legitimate
    `ENV DB_PASSWORD_FILE=/run/secrets/db`. Requiring `render()`'s own output INHERITS
    that refusal whole instead of approximating it. The marker check runs first, so text
    that is neither marked nor rendered is still reported as unmarked.

    The provenance check is what a caller reusing this writer while bypassing `render()`
    runs into. It is not a defence against code that deliberately builds a `_Rendered`.

    It is also RUNTIME ONLY, deliberately: `render()` is annotated `-> tuple[str, str]`, so
    `_Rendered` never enters the typed surface and no caller is invited to construct one.
    The cost is that a bypass type checks clean. Measured 2026-09-01: `reveal_type` on
    `render()`'s first element says `str`, and both `write(d, text.replace("a", "b"), ig)`
    and a hand written marked literal pass mypy and fail only at install time. A green
    mypy is not evidence this path is safe.

    Raises:
        DockerfileError: the text has no marker, the text did not come from `render()`, a
            file in the way is not ours, a file in the way could not be read at all, or
            the write itself failed.
    """
    planned = ((DOCKERFILE, dockerfile), (DOCKERIGNORE, dockerignore))
    on_disk: list[tuple[Path, str, str | None]] = []
    for name, text in planned:
        if not text.startswith(composegen.GENERATED_MARKER):
            raise DockerfileError(
                f"the {name} text carries no generated-file marker; refusing to write it"
            )
        if not isinstance(text, _Rendered):
            raise DockerfileError(
                f"the {name} text did not come from dockerfile.render(), so nothing has "
                "checked it for a secret; refusing to write it into the build context. "
                "Render it, and pass the result through unchanged — any string operation "
                "on rendered text yields a plain str, which counts as unrendered here."
            )
        path = server_dir / name
        verdict, existing, error = _look(path)
        if verdict is _Verdict.THEIRS:
            raise DockerfileError(
                f"{path} was not written by Yu'lon, so it was not touched and nothing was "
                "installed. Point the install at an empty folder, or move that file aside."
            )
        if verdict is _Verdict.UNREADABLE:
            raise DockerfileError(
                f"{path} could not be read ({error}), so whether Yu'lon wrote it is unknown "
                "— that is not a pass. Nothing was touched and nothing was installed. Make "
                "that file readable, or move it aside."
            )
        on_disk.append((path, text, existing))
    written: list[Path] = []
    for path, text, existing in on_disk:
        if existing == text:
            continue
        try:
            path.write_text(text, encoding="utf-8", newline="\n")
        except OSError as exc:
            raise DockerfileError(f"{path} could not be written: {exc}") from exc
        logger.info(f"wrote {path}")
        written.append(path)
    return tuple(written)
