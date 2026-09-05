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
from yulon.catalog.native import Secrets, secret_token_name
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
    declares what may never be printed, so a second secret added there arrives
    here already refused.

    **What this set covers is the DECLARATION, and the mapping is not the
    declaration.** An earlier version of this sentence read "reading the
    dataclass IS reading the declaration", which stated a convention in the
    voice of a guarantee — corrected 2026-09-04, because a probe disproved it
    the same way both times it has been tried. The mapping handed to `render()`
    is built by hand and nothing makes its keys correspond to fields of
    anything: a secret filed under a name `Secrets` never declared, or minted
    inside the function that builds the mapping, was never a field, so this set
    cannot see it (`pyplan/bug-checklist.md` §29). `SECRET_NAME_WORDS` below is
    what looks at the mapping actually handed over; this set is what the
    declaration buys, and the two cover different halves.

    The spelling comes from `native.secret_token_name()`, which is the same one
    line `CmangosInstaller._secret_tokens()` spends to build the name->value
    mapping its consumers fill from. Written twice, the two could drift, and the
    drift is silent in the dangerous direction: the mapping would carry the
    value under a new spelling while this refusal still looked for the old one.

    Measured on the unfixed code, 2026-09-01 (commit b8973c52): with this
    refusal written as one hard-coded name, a template spelling any OTHER
    secret-bearing token rendered its value straight into the Dockerfile —
    `ENV SOAP_PASSWORD=tbc-0123456789abcdef` came back from `render()` — and the
    whole suite stayed green.
    """
    return frozenset(secret_token_name(field.name) for field in fields(secret_type))


SECRET_TOKENS = secret_tokens(Secrets)
"""Every token in the installer's mapping that may not reach a file this module writes.

`composegen.generate()` took this decision for the compose files and refuses `DB_PASSWORD`
by name; the same decision, for a stronger reason, applies here. A secret in a compose
file is in a file the user owns — delete it, rotate, done. A secret in a Dockerfile is
copied into a content-addressed image LAYER, so `docker history` prints it long after the
Dockerfile is gone and undoing it means finding and deleting every image built from that
layer. Compose is "delete and rotate"; a Dockerfile is "you now have an artefact to hunt".

Since 7.3 nothing in production hands `render()` a secret: `CmangosInstaller` splits its
tokens by capability and `_write_dockerfile` passes `_public_tokens()`, the half with no
`Secrets` value in it. `_secret_tokens()` — public plus the password, which the conf tables
genuinely need, and which is why they are written 0600 while this file is written 0644 —
goes to `conf` and, when K.7 lands, to the SQL and verify. That is the first line of
defence and it lives in the CALLER.

This set is the second, kept as defence in depth against a future caller who passes the
wider mapping without noticing: reversing the split is one identifier's difference at one
call site. The only thing attacking it today is
`test_a_dockerfile_template_the_glob_cannot_see_still_cannot_bake_the_secret`, which hands
`render()` the secret-bearing mapping on purpose. The refusal lives here, in the renderer,
rather than in a test over the shipped templates, because a test protects a
LOCATION — and one was defeated by planting a template in `shared/cmangos/`, a folder its
glob never walked; the glob was widened to the whole installers tree, and
`install_wiring.py`'s `--installers-root` then points the ENGINE at a different tree
entirely, which that glob does not walk either — whereas the refusal protects the
property, for any template dir any `dockerfile_dir` ever names.

A SET, and a derived one, because the by-name version protected one NAME rather than the
property: `Secrets` is a dataclass and a second field on it is one line away.
"""

SECRET_NAME_WORDS = frozenset(
    {"PASSWORD", "PASSWD", "SECRET", "CREDENTIAL", "APIKEY", "PRIVATEKEY", "PRIVKEY"}
)
"""Words that announce a secret in a token NAME, for the secrets no declaration reaches.

**The question this answers.** `render()` is handed a `Mapping[str, str]`; the values are
opaque strings, and the only thing in this app that names a secret is `native.Secrets` —
which the caller did not have to use. So `render()` cannot know a value is a secret by
looking at it, and `SECRET_TOKENS` can only speak for values that came from a declared
field. The one piece of evidence `render()` really holds about a value it was handed is
the NAME the caller chose to file it under, and a caller who puts a password in a
build-context mapping almost always says so in the key. That is what this reads.

**Fitted to leaks that were measured, not to a threat model.** All three secrets that have
actually reached a build-context mapping in this project were filed under a `*PASSWORD`
name: `SOAP_PASSWORD` (the §29 probe, 2026-09-02, reproduced against the real module on
m910q 2026-09-04 — `render()` returned `_Rendered` with the secret in it and `write()` laid
`ENV SOAP_PASSWORD=tbc-0123456789abcdef` into the build context without a word), and
`ROOT_PASSWORD` twice, in the two mutations recorded in
`CmangosInstaller._public_tokens`'s docstring (M15, 2026-09-01; M-R2, 2026-09-02). The
other six words are the same claim about the neighbouring nouns. `PRIVKEY` was the last
one in: with `PRIVATEKEY` alone, a probe on m910q on 2026-09-04 got
`PRIVKEY -> ACCEPTED, secret in text: True` and the same for `PRIV_KEY`, which is the
one-keystroke difference `announces_a_secret()` squashes `_` to avoid, arriving through
the vocabulary instead of the spelling.

**Each word is named as a literal in
`test_the_secret_name_vocabulary_spells_every_word_and_the_measured_leaks`, and that
literal list — not the parametrized per-word test — is what goes red when a word is
deleted.** Measured 2026-09-04 on m910q before the literals existed: narrowing this set
from six words to three gave `58 passed` against `61 passed`, zero failures, because a
test generated from the set shrinks with it.

**What this rule does NOT cover, and what does now.** A secret filed under a name that
announces nothing — `FOO`, `EXTRA`, `BUILD_ARG` — passes this rule untouched. Measured
against the shipped module on m910q 2026-09-05, at `0cc637c7`, with `Secrets` declaring
one field:

    SOAP_PASSWORD  -> REFUSED: "SOAP_PASSWORD: each of those reads as a secret ..."
    BUILD_ARG      -> ACCEPTED, secret in text: True
    EXTRA          -> ACCEPTED, secret in text: True
    FOO            -> ACCEPTED, secret in text: True
    the line it wrote: ['ENV BUILD_ARG=tbc-0123456789abcdef']

That is the VALUE half of `pyplan/bug-checklist.md` §29, and it is `carries_a_secret()`'s
job, not this set's. The two are complementary and neither subsumes the other: this rule
reads a NAME and so catches a careless key holding a secret it has never been shown, while
the value rule reads a VALUE and so catches a careless key whatever it is called. This
one remains a price on the careless spelling, not a wall.

**Rejected — still — an OPTIONAL `secrets=` parameter doing that value comparison.** It
would have to default to "none declared", because since 7.3 the only production caller
passes `_public_tokens(server_dir)` and `ctx.secrets` is one scope above it. A guard no
caller invokes is a guard that never fires — [[guards-that-prove-declarations]] is four of
those — and its presence would read, to the next person, as though the value case were
handled. What `render()` takes instead is a REQUIRED keyword-only `secrets`, which is a
different object: a caller that forgets it does not lose the guard, it fails to call the
function at all (`TypeError`), and
`test_a_caller_that_forgets_the_secrets_argument_fails_instead_of_losing_the_guard` is
what says so.

**Rejected: scanning the rendered TEXT for these words.** `write()`'s docstring already
argued that down for the same words: it would refuse a legitimate
`ENV DB_PASSWORD_FILE=/run/secrets/db` line in a template. A mapping KEY is different
evidence — not a word that appears in a file, but the name a caller chose for a value it
put into a build-context mapping.

**Rejected: a `_FILE`/`_PATH` carve-out for that same false positive.** `ROOT_PASSWORD_FILE`
is one plausible spelling of the password itself, so the carve-out is the evasion route.
The asymmetry decides it: a false positive costs one refusal and a deliberate code change
with a reviewer in the room, and a false negative costs a content-addressed image layer
that has to be hunted down. A template that genuinely needs a path token can be given its
exception the loud way.
"""


def announces_a_secret(key: str) -> bool:
    """Whether a token name reads as a secret, `_` squashed before the words are looked for.

    `API_KEY` and `APIKEY` are one name, and a plain substring test over the key as spelled
    would catch the second and miss the first — a difference of one keystroke that nobody
    would read as a security decision. The same goes for case: `soap_password` is
    `SOAP_PASSWORD`. The `.upper()` here survived as a mutant until 2026-09-04 — dropped,
    `test_dockerfile.py` stayed `61 passed` on m910q, because every case handed the rule a
    key already in upper case; `test_the_case_of_a_key_is_not_a_way_past_the_name_rule`
    is the one that fails without it.
    """
    squashed = key.upper().replace("_", "")
    return any(word in squashed for word in SECRET_NAME_WORDS)


MIN_CONTAINED_SECRET = 8
"""At this length a secret is looked for INSIDE a token value; below it, only as the whole.

**Why a floor at all.** Containment against a very short secret refuses everything.
Measured on m910q 2026-09-05 with `server_dir=/tmp/fixedsrv/srv`, over the 34 distinct
values the three shipped CMaNGOS `_public_tokens()` mappings produce: the empty string is
contained in every one of them, and 31 of the 36 single alphanumeric characters are
contained in at least one — `a` alone is in 14 (`/opt/mangos`, `characters`, …). A rule
that refused those would not be strict, it would be an install that can never run.

**The server directory is named because those two counts move with it.** Three of the 34
values carry an 8-hex digest of the install path (`IMAGE_TAG` = `native-f33d5256`,
`PROJECT_NAME` = `yulon-wow-tbc-f33d5256`, …), so a count over the values' CHARACTERS
answers differently in another folder. This paragraph said "30 of the 36" and "`a` alone
is in 16" until 2026-09-05, from a run under per-game temporary directories; the numbers
above are the same probe with the directory fixed. Nothing the floor rests on moves — 34
distinct values, `""` in all 34, `mangos` in five, `password` in none — and these two
counts are here to show that a floor is needed at all, not to decide where it goes.

**Why 8: a lower bound that is measured, an upper bound that is a promise.** The lower
bound is a real collision in the shipped mapping: `mangos`, six characters, is contained
in five shipped token values, so a six-character password spelled that way would refuse
every CMaNGOS install. The floor has to be above 6. The upper bound is coverage — the
floor must be at or below every secret THIS APP ITSELF produces, or containment silently
degrades to equality for a real install. Measured on m910q 2026-09-05 by calling
`resolve_secrets()` on an empty server dir through `families.family_for()` for every
shipped entry: `wow-tbc` 20, `wow-vanilla` 24, `wow-tortoise` 25 (`<prefix><16 hex>`), and
`wow-wotlk` 8 — its fixed `password`, the shortest anything in `catalog.json` yields, and
contained in none of the 34 values. 8 is the largest number that clears both bounds.

**What the floor is NOT answerable to — the correction of 2026-09-05.** This paragraph
used to say 8 "covers every secret this app can hand `render()`", read off `catalog.json`.
On the live path `resolve_secrets()` does not read `catalog.json` at all: every entry that
renders a Dockerfile is `mode: generated`, and for one of those it returns
`Secrets(<server_dir>/<password.file> read and stripped)` TAKEN AS WRITTEN when the file
is already there. Measured through `CmangosInstaller.resolve_secrets()` on m910q
2026-09-05: a `.db_password` holding `abc` yields a three-character secret and drops this
comparison to equality; one holding `characters` yields a ten-character one and
containment. So the values the app MINTS have a floor and the file the user owns has none,
and no number here can give it one.
`test_a_user_written_password_below_the_floor_falls_to_equality_and_not_to_silence`
measures that route rather than describing it.

**And why not a larger number, which is the tempting answer.** Because raising it buys
nothing measurable. The collision surface does not empty out with length: same box, same
day, same fixed server dir, the shipped values yield 102 distinct 8-character substrings,
78 distinct 12-character ones, and the longest value is 29 characters
(`yulon.local/cmangos-tortoise-`), so a containment collision remains *possible* at every
length a password can have. (The substring counts move with the server dir for the same
reason as above; that a collision exists at every length does not.) What makes 8 safe is
not that collisions stop, it is that the strings which collide are catalog-derived
fragments — `mariadb:`, `/opt/man`, `haracter` — and none of them is a password anybody
sets. A bigger floor would drop coverage of `wow-wotlk`'s real declared secret in exchange
for that same non-guarantee.

**Below the floor it is equality, not silence.** All four leaks ever measured into this
mapping put the password in VERBATIM under some other key — M15 and M-R2
(`CmangosInstaller._public_tokens`'s docstring), §29's `SOAP_PASSWORD` probe, and the
`BUILD_ARG` probe above — so equality still catches the shape that has actually happened,
even for a one-character password. What equality cannot see is a short secret embedded in
a longer value, and that is stated rather than fixed. No entry DECLARES one; a user who
writes a short password into their own `.db_password` has one, and that is the case the
test named above pins.

An EMPTY secret matches nothing at all, by either test. It is in every string, so
containment would refuse every install; and equality on it would refuse any empty token
value. Measured the same day: no shipped `_public_tokens()` mapping has an empty value
today, so the second half of that is a cost nobody is paying — but "no empty values,
today" is a fact about the catalog, and the guard should not depend on it. An empty
password is not a secret worth a refusal in any case.
"""


def carries_a_secret(value: str, secret: str) -> bool:
    """Whether a token VALUE leaks `secret` — contained if it is long enough, else equal.

    The asymmetry is `MIN_CONTAINED_SECRET`'s, argued there with the measurements.
    Containment rather than equality above the floor because a leak does not have to be
    tidy: `--db-pass=<the password>` under one key is the same image layer as the bare
    value under another, and equality alone would wave it through.
    """
    if not secret:
        return False
    if len(secret) < MIN_CONTAINED_SECRET:
        return value == secret
    return secret in value


class DockerfileError(RuntimeError):
    """A template could not be rendered, or a file in the way is not ours to replace."""


def render(template_dir: Path, tokens: Mapping[str, str], *, secrets: Secrets) -> tuple[str, str]:
    """(Dockerfile text, .dockerignore text), each beginning with the marker as a `#` comment.

    One `fill()` for every template in the app — `composegen.fill`, which refuses an
    unfilled `{{TOKEN}}` — so a Dockerfile cannot ship a literal placeholder that `docker
    build` would happily read as a path. Unused tokens are fine, and the caller relies on
    that: `CmangosInstaller._write_dockerfile` passes the whole of
    `CmangosInstaller._public_tokens(server_dir)` — `entry_tokens(entry)` plus the
    per-install keys (`REALM_HOST`, the three ports, `PROJECT_NAME`, `IMAGE_PREFIX`,
    `IMAGE_TAG`) — while the two templates spend only `CORE_DIR` and `MAKE_JOBS`.

    Since 7.3 the mapping that arrives here from production carries NO secret; the
    key-drop below and the by-name refusal further down are kept against a future caller
    who passes the wider `_secret_tokens()` mapping instead, which is one identifier's
    difference at that call site.

    The text comes back LF whatever the worktree holds. The shipped templates are LF in
    git's index and CRLF in a Windows checkout under `core.autocrlf=true`; reading them
    in text mode translates that away, and `write()` is what keeps it translated away.

    Every token in `SECRET_TOKENS` is dropped from the mapping before either half is
    filled — belt to the refusal's braces, exactly as `composegen.generate()` does it. A
    template that spelled one is refused by name below; were that refusal ever removed,
    the token would then be UNFILLED rather than quietly rendering the secret into the
    build context.

    A key whose NAME announces a secret while corresponding to no `Secrets` field is
    refused outright, before anything is read or filled — `SECRET_NAME_WORDS`. It is
    refused on the MAPPING and not on the rendered text, deliberately: §29's own words
    about the case that preceded it are *"nothing exploited it; the exposure was one
    template edit away"*, so a rule that waited for a template to spell the token would be
    a guard over today's templates, which is the LOCATION-shaped protection this module
    has already watched fail twice. It also means the rule holds for a `template_dir`
    nobody has seen.

    **`secrets` is the VALUE half of the same question, and it is required.** The name
    rule can only read the name a caller chose; a secret filed under `BUILD_ARG` walked
    past it, measured on m910q 2026-09-05 at `0cc637c7` and quoted in
    `SECRET_NAME_WORDS`. So the caller hands over the declaration itself and this
    function refuses any key carrying one of its values — `carries_a_secret()` decides
    what carrying means. The two rules are complementary rather than layered: the name
    rule catches a careless spelling around a value nobody here has seen, and this one
    catches a careless key around a value that is provably the install's own secret.

    It is a `Secrets` and not a bag of strings for the reason `SECRET_TOKENS` is derived
    rather than listed: a second field on `native.Secrets` extends this refusal on the
    day it is added, with nobody to remember. `fields()` is asked of the INSTANCE, so a
    subclass carrying an extra secret answers with both — the same choice
    `cmangos.secret_token_map()` made, and for the same reason.

    It is REQUIRED and keyword-only, which is the whole difference between this and the
    optional parameter §29 rejected. Today's sole production caller is
    `CmangosInstaller._write_dockerfile`, which holds `ctx.secrets` one frame above the
    `_public_tokens(server_dir)` it passes as `tokens`; the narrow parameter list of
    `_public_tokens()` is untouched, so nothing about 7.3's capability split is undone —
    the secret is named at the RENDER call, as the thing that must not be emitted, not
    added to the mapping. A second caller who forgets it raises `TypeError` rather than
    quietly rendering without the guard.

    **The exemption is the tokens this module DROPS, not the tokens the declaration
    names.** A value may carry a secret only under a key in `SECRET_TOKENS`, because
    those are exactly the keys removed from the mapping below — that is the whole purpose
    of `_secret_tokens()`'s mapping, and `test_a_dockerfile_template_the_glob_cannot_see…`
    hands it over on purpose. A subclass's extra secret filed under its own declared
    token is NOT exempt, and should not be: `SECRET_TOKENS` comes from `native.Secrets`,
    so such a key would survive the drop and be filled straight into the build context.

    **Rejected: also scanning the rendered TEXT for these values.** `render()` now holds
    them, so it could — and `write()`'s docstring only rejected the NAME-scanning version.
    What it would add is a template that hard-codes a real secret, and measured
    2026-09-05 that set is empty in both directions: a GENERATED password is minted per
    install (`<prefix><16 hex>`), so no committed template can contain one, and the only
    FIXED password in the shipped catalog is `wow-wotlk`'s literal `password` — eight
    characters of English, belonging to an entry whose family does not call this function.
    Against that, containment over a whole Dockerfile is a much larger false-positive
    surface than containment over one token value: a future template line reading
    `# the password file is mounted at run time` would refuse that entry's install
    outright. No shipped template spells the word today (measured, same box), so this is
    a cost nobody pays yet and a coverage nobody gains yet — reconsider it the day a
    family with a short fixed password renders a Dockerfile.

    Both halves come back as `_Rendered`, which is the only text `write()` will lay down.

    Raises:
        DockerfileError: a template could not be read, a placeholder was left unfilled, a
            template names a `SECRET_TOKENS` placeholder such as `{{DB_PASSWORD}}`, the
            mapping files a secret-sounding value under a name no `Secrets` field
            declares, or the mapping carries a declared secret's VALUE under a key this
            module does not drop.
        TypeError: `secrets` was not passed. Deliberate, and see above.
    """
    # The VALUE rule runs FIRST, and the order is a decision about the sentence a reader
    # gets when a key trips both — `SOAP_PASSWORD` holding the install's real password,
    # say. The name rule reports a guess about spelling ("this READS as a secret"); this
    # one reports something proved by comparison against the declaration ("this IS the
    # value of DB_PASSWORD"), and the remedies differ: a merely secret-sounding key may
    # be a false alarm to rename, while a key holding the real password has to go.
    # `test_a_key_that_trips_both_rules_is_reported_by_the_one_that_proved_it` pins it.
    declared = {
        secret_token_name(field.name): getattr(secrets, field.name) for field in fields(secrets)
    }
    carrying: list[str] = []
    for key, value in sorted(tokens.items()):
        if key in SECRET_TOKENS:
            continue
        named = sorted(name for name, secret in declared.items() if carries_a_secret(value, secret))
        if named:
            carrying.append(f"{key} (the value declared as {', '.join(named)})")
    if carrying:
        raise DockerfileError(
            f"{'; '.join(carrying)} — and this mapping is rendered into the build context, "
            "where a Dockerfile is copied into an image layer that `docker history` prints "
            "long after the file is deleted. A secret may only ride in this mapping under a "
            "token this renderer drops "
            f"({', '.join(sorted(SECRET_TOKENS))}), and only towards a consumer that needs "
            "it — `_secret_tokens()`, whose conf files are written 0600. Drop the key, or "
            "file the value under its declared token. The value itself is not printed here."
        )
    undeclared = sorted(
        key for key in tokens if key not in SECRET_TOKENS and announces_a_secret(key)
    )
    if undeclared:
        raise DockerfileError(
            f"{', '.join(undeclared)}: each of those reads as a secret and matches no field "
            "of `native.Secrets`, so nothing here can drop them or refuse a template that "
            "spells one — and this mapping is rendered into the build context, where a "
            "Dockerfile is copied into an image layer that `docker history` prints long "
            "after the file is deleted. Whichever of them IS a secret, declare it on "
            "`native.Secrets`: the drop and the by-name refusal are both derived from that "
            "declaration, and the consumers that genuinely need the value get it through "
            "`_secret_tokens()`, whose conf files are written 0600. Whichever is not, rename "
            "it — this rule reads the name, because the value it was handed is an opaque "
            "string."
        )
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
    secret VALUES — THIS function's signature does not take a `Secrets` and is not being
    given one — so a re-check here could only scan for the token NAMES: it would miss a
    password stored under any other name (exactly the text above) and refuse a legitimate
    `ENV DB_PASSWORD_FILE=/run/secrets/db`. Requiring `render()`'s own output INHERITS
    that refusal whole instead of approximating it. The marker check runs first, so text
    that is neither marked nor rendered is still reported as unmarked.

    That inheritance is now worth more than it was, and the sentence above needed
    correcting rather than deleting: since §29's value half landed, `render()` DOES take
    the declaration, so what `write()` inherits includes a refusal of any mapping value
    that carried a declared secret — the thing a content re-check here could never have
    done. What it still does not inherit is anything about text a caller assembled
    itself, which is the provenance rule's whole subject.

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
