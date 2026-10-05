"""A fingerprint of exactly what Docker builds a server from, so a finished build can be kept.

T224. A Rebuild whose build finished but whose containers could not be replaced keeps
that build, and the next Rebuild uses it only when this fingerprint, taken again,
is the same. The fingerprint is a sha256 over one canonical stream:

* the format tag and the sorted image refs;
* the bytes of the build overlay (`docker-compose.build.yml`), of the root
  `.dockerignore` and of every `dockerfile:` the overlay names. BuildKit reads
  the Dockerfile and the `.dockerignore` on their own even when the walk leaves
  them out, which `*` does on every family that renders one;
* the entries of the build context, filtered by the `.dockerignore`, depth first,
  with the names in each folder sorted by their UTF-8 bytes. A regular file gives
  its path, size, content and mode; a folder its path and mode; a symlink its
  path, mode and target string (it is never followed). An excluded folder that
  holds a re-admitted entry is given too, because BuildKit sends it.

Which entries depends on the recipe (T230). BuildKit sends only the context paths
the built stages COPY, ADD or bind-mount (`llb.FollowPaths`), not the whole
filtered context. A recipe whose bytes are in `KNOWN_RECIPES` was read by hand,
and only its `reads` are walked: the paths themselves, what is inside them and the
folders on the way to them. A `.git` folder inside them counts as the text of its
HEAD plus the commit HEAD names (`_git_head`), because that is all the recipe's
one git reader takes from it, and only while that reader is byte for byte the file
that was read. Any other recipe, or a `.git` whose HEAD cannot be followed, is
walked as the whole filtered context, exactly as before T230.

Mtimes are not hashed: a patch applied and then reverted gives the same
fingerprint, which is the case of 2026-10-04 (an uncommitted patch was built and
later reverted under the same commit). Owner, uid and gid are not hashed either:
`COPY` without `--chown` sets them to root.

Docker's matching rules are followed, not approximated. The sources are
moby/patternmatcher (`ignorefile.ReadAll`, `New`, `Pattern.compile`,
`Pattern.match`, `MatchesOrParentMatches`, `MatchesUsingParentResults`) and
tonistiigi/fsutil `filter.go` (BuildKit's walk), read on 2026-10-04, and the
documented rules at https://docs.docker.com/build/concepts/context/#dockerignore-files:

* A line runs up to `\\n` (bufio's trailing `\\r` is trimmed with the other
  whitespace below, so it is not dropped separately), and a UTF-8 byte
  order mark is dropped from the first line. A line whose FIRST character is `#`
  is a comment; only then is the line trimmed, so ` #x` is the pattern `#x`.
* A leading `!` marks an exception. The pattern is cleaned like Go's
  `filepath.Clean`, and one leading `/` is removed, so `/foo` and `foo` are the
  same pattern. Patterns are relative to the context root: `foo` names `./foo`
  and never `a/foo`.
* `*` is any run of characters except `/`, and `?` is one character except `/`.
  `**` matches any number of folders, including none. A pattern that ends in
  `**` is a prefix match. A pattern that starts with `**` and has no other
  wildcard is moby's suffix match: `**foo` matches `barfoo`, and `**/foo`
  matches `foo`.
* A pattern matches a path when it matches the path or any of its parent
  folders, and the LAST pattern that matches decides.

Anything this module cannot follow exactly answers None, and None means "no
answer": the build is then not kept. It is never a guess. The cases are:

* in the `.dockerignore`: a character class (`[...]`, where Go's regexp and
  `filepath.Match` disagree), a backslash (an escape on Linux, a separator on
  Windows), a `^` (moby passes it to the regexp unescaped, as an anchor), and a
  bare `!` (Docker refuses it);
* a path on which `MatchesOrParentMatches` and BuildKit's
  `MatchesUsingParentResults` give different answers (`a/b` then `!a` is one);
* a per-Dockerfile `<Dockerfile>.dockerignore`, which replaces the root one;
* in the overlay: a context that is not the server folder, a recipe outside it,
  a value taken from the environment (`$`), an inline recipe, or
  `additional_contexts`; also a `build:` key in the base or override compose
  file, and a missing recipe;
* in the tree: a name that is not UTF-8, a socket, FIFO or device that Docker
  would send, a junction, any OSError, a file that changed while it was read,
  and either cap (`MAX_BYTES`, `MAX_ENTRIES`).

Deliberately left out: Yu'lon's own `.yulon*` files at the context root. They
are never compiled, and writing the record of a kept build would otherwise change
the fingerprint that record holds. On a known recipe two more things are left out,
each with its proof in the T230 plan §1:

* every context path no built stage reads. On AzerothCore's that is the
  Maintenance backups and an update's database copy (`sql_scripts/backups`), the
  module update checks' clones (`sql_scripts/clones`, `ale_scripts`) and the
  compose files a settings save or a repair rewrites;
* everything in a `.git` folder but HEAD and its commit: fetched objects and refs,
  FETCH_HEAD, the index, ORIG_HEAD and the logs. The compile bind-mounts the root
  `.git`, and `genrev.cmake` runs `git describe --long --match 0.1 --dirty=+
  --abbrev=12 --always`, `git show -s --format=%ci` and `git rev-parse
  --abbrev-ref HEAD` there; no other CMake file at the pin, nor any of the 22
  catalog modules, runs git. Of what those three print, only HEAD and its commit
  can change: the regex at genrev.cmake:78 strips `0.1-` and `N-g`, so whether a
  `0.1` tag exists leaves the same hash; and `--dirty` always prints `+`, because
  the container's work tree holds only the five copied paths, so `apps/` and
  `data/`, which HEAD tracks, read as deleted whatever the index says.
  `modules/*/.git` is copied, into the db-import image, but read by no step: the
  db-import service bind-mounts `./modules` over that copy (`base.yml.tmpl`).
  The same rule is applied to a `.git` folder anywhere else under the reads (in
  `deps` or `src`, say). At the pin that is sound for the same reason: the only git
  run is genrev's, in `/azerothcore`, which reads the root `.git` alone, and the
  build stage that copies `deps` and `src` passes on only what it compiled.

What that leaves uncovered changes at most the world's version line: a git reader
other than `genrev.cmake` after an update that leaves it unchanged, or a custom
module whose build reads its own git metadata beyond HEAD (R1); HEAD's 12-digit
abbreviation growing on a prefix collision (R2); a tag and a branch of the same
name making `--abbrev-ref` print `heads/<name>` (R3). Not covered on any recipe,
and the player is told so: the base image and the packages a build fetches.

The walk holds one sorted folder listing per level of depth and reads every
file through one fixed buffer, so memory stays bounded however large the tree is.
"""

from __future__ import annotations

import hashlib
import os
import posixpath
import re
import stat
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from yulon.catalog.composegen import BASE_FILE, BUILD_FILE, OVERRIDE_FILE
from yulon.catalog.git_head import resolve_head
from yulon.log import get_logger

log = get_logger(__name__)

FORMAT = b"yulon-build-fp/1"
DOCKERIGNORE = ".dockerignore"
MAX_BYTES = 4 * 1024**3
"""Bytes read in all (recipe files and context), past which there is no answer.

About 1 GB of a Centurion checkout reaches the daemon (plan §2). 4 GB keeps an
upstream context, such as AzerothCore's, from turning into an unbounded read."""
MAX_ENTRIES = 200_000
"""Folder entries looked at, admitted or not, past which there is no answer (plan §2)."""
OWN_PREFIX = ".yulon"
CHUNK = 1024 * 1024
MAX_RECIPE_BYTES = 16 * 1024 * 1024
"""The overlay and the `.dockerignore` are read whole; one larger than this is not ours."""

# Go's unicode.IsSpace, the set strings.TrimSpace trims. Python's str.strip()
# also trims U+001C..U+001F, so the set is spelled out.
_GO_SPACE = "\t\n\v\f\r \x85\xa0" + "".join(
    chr(c) for c in (0x1680, *range(0x2000, 0x200B), 0x2028, 0x2029, 0x202F, 0x205F, 0x3000)
)
# bufio.Scanner's default limit. Docker treats a longer line as an error.
_MAX_LINE = 64 * 1024
# The characters Pattern.compile escapes. Every other character except the
# wildcards goes into its regexp as it is, which is why `^` is refused.
_REGEX_SPECIAL = frozenset(".+()|{}$")
_REFUSED = frozenset("[]\\^")

_COMMENT = re.compile(r"^\s*#")
_CONTEXT = re.compile(r"^\s*context\s*:\s*(?P<value>.*?)\s*$")
_DOCKERFILE = re.compile(r"^\s*dockerfile\s*:\s*(?P<value>.*?)\s*$")
_UNCOVERED_KEY = re.compile(r"^\s*(dockerfile_inline|additional_contexts)\s*:")
_BUILD_KEY = re.compile(r"^\s+build\s*:")


@dataclass(frozen=True)
class KnownRecipe:
    """A recipe read by hand: the context paths its stages take, and the file whose git it runs."""

    reads: tuple[str, ...]
    """Context paths, slash form, that the stages Yu'lon builds COPY, ADD or bind-mount."""
    git_reader: tuple[str, str]
    """(path, sha256) of the one file whose git calls were read; `.git` counts as HEAD only
    while that file is byte for byte the one that was read."""


KNOWN_RECIPES: dict[str, KnownRecipe] = {
    # mod-playerbots/azerothcore-wotlk `apps/docker/Dockerfile` at the catalog pin 7f12e89e,
    # stages skeleton, build, runtime, authserver, worldserver, db-import and client-data (the
    # four targets `wow-wotlk/native/build.yml.tmpl` builds; `tools` is not built). T230 plan §1.
    "e87bc1bd18f94bbf1705b81438b0caf7a1c8c8c8a0330cdaf08511f0f3ae7964": KnownRecipe(
        reads=(".git", "CMakeLists.txt", "apps", "conf", "data", "deps", "modules", "src"),
        git_reader=(
            "src/cmake/genrev.cmake",
            "a27f319585605516ed82601d87a7785a135ae3b3d0b725d1bf4f24615b6d342b",
        ),
    ),
}
"""Recipes recognised by the sha256 of their bytes. Any other recipe walks the whole context."""
MAX_HEAD_BYTES = 4096
"""A `.git/HEAD` larger than this is not one git wrote; its folder is then walked byte for byte."""


class _NoAnswer(Exception):
    """Something this module cannot follow exactly; the fingerprint is then None."""


def _go_clean(path: str) -> str:
    """Go's `filepath.Clean` on a slash path; posixpath.normpath keeps a leading `//`, Go not."""
    cleaned = posixpath.normpath(path)
    if cleaned.startswith("//"):
        cleaned = "/" + cleaned.lstrip("/")
    return cleaned


def _go_trim(text: str) -> str:
    return text.strip(_GO_SPACE)


@dataclass(frozen=True)
class _Pattern:
    """One compiled pattern, typed the way `Pattern.compile` types it."""

    text: str
    exception: bool
    kind: str  # "exact", "prefix", "suffix" or "regex"
    regex: re.Pattern[str] | None
    segments: tuple[re.Pattern[str], ...] | None
    """One matcher per segment, or None when the pattern holds `**` and can span folders."""

    def match(self, path: str) -> bool:
        if self.kind == "exact":
            return path == self.text
        if self.kind == "prefix":
            return path.startswith(self.text[:-2])
        if self.kind == "suffix":
            suffix = self.text[2:]
            if path.endswith(suffix):
                return True
            return suffix.startswith("/") and path == suffix[1:]
        assert self.regex is not None
        return self.regex.fullmatch(path) is not None

    def may_match_under(self, folder: tuple[str, ...]) -> bool:
        """Whether this pattern could match some path strictly inside `folder`.

        Without `**`, neither `*` nor `?` crosses a `/`, so the pattern matches
        only paths with as many segments as it has. It can reach inside `folder`
        only when it has more segments and its leading ones match the folder's.
        With `**` the answer is yes. That costs a longer walk and never a wrong
        answer.
        """
        if self.segments is None:
            return True
        if len(self.segments) <= len(folder):
            return False
        return all(seg.fullmatch(name) for seg, name in zip(self.segments, folder, strict=False))


def _segment(text: str) -> re.Pattern[str]:
    return re.compile(
        "".join("[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c) for c in text)
    )


def _compile(text: str, exception: bool) -> _Pattern:
    """moby's `Pattern.compile` for the characters this module follows; the rest refuse."""
    if _REFUSED & set(text):
        raise _NoAnswer(f"the .dockerignore pattern {text!r} uses [, ], \\ or ^")
    regex = ""
    kind = "exact"
    i = 0
    first = True
    while i < len(text):
        ch = text[i]
        i += 1
        if ch == "*" and i < len(text) and text[i] == "*":
            i += 1
            if i < len(text) and text[i] == "/":
                i += 1
            if i >= len(text):
                if kind == "exact":
                    kind = "prefix"
                else:
                    regex += ".*"
                    kind = "regex"
            else:
                regex += "(.*/)?"
                kind = "regex"
            if first:
                kind = "suffix"
        elif ch == "*":
            regex += "[^/]*"
            kind = "regex"
        elif ch == "?":
            regex += "[^/]"
            kind = "regex"
        elif ch in _REGEX_SPECIAL:
            regex += "\\" + ch
        else:
            regex += re.escape(ch)
        first = False
    segments = None if "**" in text else tuple(_segment(s) for s in text.split("/"))
    return _Pattern(
        text=text,
        exception=exception,
        kind=kind,
        regex=re.compile(regex) if kind == "regex" else None,
        segments=segments,
    )


@dataclass(frozen=True)
class _Decision:
    """One path's answer, and what its children inherit from it in each of Docker's two matchers."""

    excluded: bool
    ancestors: tuple[bool, ...]
    """Per pattern: it matches this path or one of its parents (`MatchesOrParentMatches`)."""
    inherited: tuple[bool, ...]
    """Per pattern: the `MatchInfo` BuildKit's walk hands this path's children."""


@dataclass(frozen=True)
class Rules:
    """A parsed `.dockerignore`: its patterns, in file order."""

    patterns: tuple[_Pattern, ...] = ()

    def decide(self, path: str, parent: _Decision | None) -> _Decision:
        """`path`'s answer, given its parent folder's (None at the context root)."""
        own: dict[int, bool] = {}

        def matches(i: int) -> bool:
            if i not in own:
                own[i] = self.patterns[i].match(path)
            return own[i]

        # MatchesOrParentMatches, carried down the walk. A pattern counts when it
        # matches this path or matched a parent, and the last one that counts
        # decides.
        ancestors = tuple(
            (parent is not None and parent.ancestors[i]) or matches(i)
            for i in range(len(self.patterns))
        )
        classic = False
        for i, pattern in enumerate(self.patterns):
            if pattern.exception == classic and ancestors[i]:
                classic = not pattern.exception
        # MatchesUsingParentResults, as fsutil walks with it. A pattern that was
        # skipped at a parent is NOT inherited, and that is where the two differ.
        buildkit = False
        inherited = [False] * len(self.patterns)
        for i, pattern in enumerate(self.patterns):
            match = parent is not None and parent.inherited[i]
            if not match:
                if pattern.exception != buildkit:
                    continue
                match = matches(i)
            inherited[i] = match
            if match:
                buildkit = not pattern.exception
        if classic != buildkit:
            raise _NoAnswer(f"Docker's two matchers disagree about {path!r}")
        return _Decision(classic, ancestors, tuple(inherited))

    def excludes(self, path: str) -> bool | None:
        """Whether Docker leaves out `path` (slash-separated, from the root); None if unsure.

        Decided the way the walk decides it, folder by folder from the root, so a
        direct question and the fingerprint cannot disagree.
        """
        parts = path.split("/")
        decision: _Decision | None = None
        try:
            for depth in range(1, len(parts) + 1):
                decision = self.decide("/".join(parts[:depth]), decision)
        except _NoAnswer:
            return None
        assert decision is not None
        return decision.excluded

    def may_admit_under(self, folder: tuple[str, ...]) -> bool:
        """Whether an exception could re-admit something inside the excluded `folder`."""
        return any(p.exception and p.may_match_under(folder) for p in self.patterns)


def _read_patterns(text: str) -> list[str]:
    """`ignorefile.ReadAll`: the pattern strings, `!` kept, in file order."""
    if any(ch in text for ch in "\x1c\x1d\x1e\x1f"):
        raise _NoAnswer("the .dockerignore holds a character Python trims and Go does not")
    out: list[str] = []
    for number, line in enumerate(text.split("\n")):
        if len(line.encode("utf-8")) > _MAX_LINE:
            raise _NoAnswer("a .dockerignore line is longer than Docker reads")
        if number == 0 and line.startswith("\ufeff"):
            line = line[1:]
        if line.startswith("#"):
            continue
        pattern = _go_trim(line)
        if not pattern:
            continue
        invert = pattern.startswith("!")
        if invert:
            pattern = _go_trim(pattern[1:])
        if pattern:
            pattern = _go_clean(pattern)
            if len(pattern) > 1 and pattern.startswith("/"):
                pattern = pattern[1:]
        out.append("!" + pattern if invert else pattern)
    return out


def _parse(text: str) -> Rules:
    patterns: list[_Pattern] = []
    for raw in _read_patterns(text):
        # patternmatcher.New trims and cleans once more, then splits off "!".
        cleaned = _go_clean(_go_trim(raw))
        exception = cleaned.startswith("!")
        if exception:
            if len(cleaned) == 1:
                raise _NoAnswer('Docker refuses the .dockerignore pattern "!"')
            cleaned = cleaned[1:]
        patterns.append(_compile(cleaned, exception))
    return Rules(tuple(patterns))


def parse_dockerignore(text: str) -> Rules | None:
    """The rules of a `.dockerignore`, or None for anything this module does not follow exactly."""
    try:
        return _parse(text)
    except _NoAnswer as exc:
        log.info("build fingerprint: %s", exc)
        return None


# --- the stream --------------------------------------------------------------------------------


class _Stream:
    """The one sha256 everything goes into; every field is length-prefixed so none runs on."""

    def __init__(self, max_bytes: int, max_entries: int) -> None:
        self._digest = hashlib.sha256()
        self._buffer = memoryview(bytearray(CHUNK))
        self._max_bytes = max_bytes
        self._max_entries = max_entries
        self._bytes = 0
        self._entries = 0

    def put(self, data: bytes) -> None:
        self._digest.update(len(data).to_bytes(8, "big"))
        self._digest.update(data)

    def hexdigest(self) -> str:
        return self._digest.hexdigest()

    def count_entries(self, n: int) -> None:
        self._entries += n
        if self._entries > self._max_entries:
            raise _NoAnswer(f"the build context has more than {self._max_entries} entries")

    def _spend(self, size: int) -> None:
        self._bytes += size
        if self._bytes > self._max_bytes:
            raise _NoAnswer(f"the build context holds more than {self._max_bytes} bytes")

    def file(self, path: Path, *, keep: bool = False) -> tuple[int, bytes]:
        """Hash one regular file's size and bytes, which must hold still: (mode, kept bytes)."""
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        kept = bytearray()
        with os.fdopen(os.open(path, flags), "rb", buffering=0) as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise _NoAnswer(f"{path} is not a regular file")
            if keep and before.st_size > MAX_RECIPE_BYTES:
                raise _NoAnswer(f"{path} is too large to be a recipe file")
            self._spend(before.st_size)
            self._digest.update(before.st_size.to_bytes(8, "big"))
            read = 0
            while count := handle.readinto(self._buffer):
                chunk = self._buffer[:count]
                self._digest.update(chunk)
                if keep:
                    kept += chunk
                read += count
                if read > before.st_size:
                    break
            after = os.fstat(handle.fileno())
        if read != before.st_size or (after.st_size, after.st_mtime_ns) != (
            before.st_size,
            before.st_mtime_ns,
        ):
            raise _NoAnswer(f"{path} changed while it was read")
        return stat.S_IMODE(before.st_mode), bytes(kept)


def _utf8(name: str) -> bytes:
    try:
        return name.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _NoAnswer(f"the name {name!r} is not UTF-8") from exc


def _overlay_recipes(text: str, overlay: str) -> list[str]:
    """The `dockerfile:` paths the overlay names, once what its bytes do not cover is refused."""
    recipes: list[str] = []
    for line in text.splitlines():
        if _COMMENT.match(line):
            continue
        if "$" in line:
            raise _NoAnswer(f"{overlay} takes a value from the environment")
        if _UNCOVERED_KEY.match(line):
            raise _NoAnswer(f"{overlay} uses a build key the fingerprint does not cover")
        if (found := _CONTEXT.match(line)) and found["value"].strip("'\"") not in (".", "./"):
            raise _NoAnswer(f"{overlay} builds from a context other than the server folder")
        if found := _DOCKERFILE.match(line):
            recipe = found["value"].strip("'\"")
            cleaned = _go_clean(recipe)
            if not recipe or "\\" in recipe or cleaned.startswith(("/", "..")):
                raise _NoAnswer(f"{overlay} names a recipe outside the server folder")
            recipes.append(cleaned)
    if not recipes:
        raise _NoAnswer(f"{overlay} names no dockerfile")
    return sorted(set(recipes))


def _no_build_key_elsewhere(context: Path, compose_files: Sequence[str]) -> None:
    for name in compose_files:
        path = context / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not _COMMENT.match(line) and _BUILD_KEY.match(line):
                raise _NoAnswer(f"{name} has a build: key the fingerprint does not cover")


@dataclass
class _Frame:
    parts: tuple[str, ...]
    decision: _Decision | None
    entries: Iterator[os.DirEntry[str]]
    mode: int
    sent: bool


def _sorted_entries(path: str, stream: _Stream) -> Iterator[os.DirEntry[str]]:
    with os.scandir(path) as found:
        entries = sorted(found, key=lambda entry: _utf8(entry.name))
    stream.count_entries(len(entries))
    return iter(entries)


def _send_folders(stream: _Stream, stack: list[_Frame]) -> None:
    """Give every folder on the way down that has not been given yet, outermost first."""
    for frame in stack:
        if not frame.sent:
            stream.put(b"d")
            stream.put(_utf8("/".join(frame.parts)))
            stream.put(oct(frame.mode).encode("ascii"))
            frame.sent = True


def _on_a_read(rel: str, reads: tuple[str, ...]) -> bool:
    """Whether `rel` is a read, inside one, or a folder on the way to one; whole segments only."""
    return any(
        rel == read or rel.startswith(read + "/") or read.startswith(rel + "/") for read in reads
    )


@dataclass(frozen=True)
class _Known:
    """Known mode: what the recognised recipes read, and whether `.git` counts as its HEAD."""

    reads: tuple[str, ...]
    git_readers: tuple[str, ...] | None
    """The git readers' paths when every one is the file that was read; None walks `.git`."""


def _small_file(path: Path, limit: int) -> bytes | None:
    """A regular file's bytes, not followed through a link; None if it cannot be read whole."""
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        with os.fdopen(os.open(path, flags), "rb", buffering=0) as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                return None
            data = handle.read(limit + 1)
    except OSError:
        return None
    return data if len(data) <= limit else None


def _git_readers_unchanged(context: Path, readers: Sequence[tuple[str, str]]) -> bool:
    for path, sha in readers:
        data = _small_file(context / path, MAX_RECIPE_BYTES)
        if data is None or hashlib.sha256(data).hexdigest() != sha:
            return False
    return True


def _git_head(stream: _Stream, gitdir: Path, rel: str, readers: tuple[str, ...]) -> bool:
    """Put what the git reader takes from `gitdir`: HEAD's text and its commit (T230).

    False, with nothing put, when that cannot be said exactly: a worktree's
    `commondir`, a reftable, a HEAD that is not a small regular UTF-8 file, or one
    that does not resolve to a commit. The folder is then walked byte for byte.
    """
    if (gitdir / "commondir").exists() or (gitdir / "reftable").exists():
        return False
    head = _small_file(gitdir / "HEAD", MAX_HEAD_BYTES)
    if head is None:
        return False
    try:
        commit = resolve_head(gitdir, head.decode("utf-8"))
    except (OSError, UnicodeError):
        return False
    if commit is None:
        return False
    stream.put(b"git")
    stream.put(_utf8(rel))
    stream.put(len(readers).to_bytes(8, "big"))
    for reader in readers:
        stream.put(_utf8(reader))
    stream.put(head)
    stream.put(commit.encode("ascii"))
    return True


def _walk(stream: _Stream, context: Path, rules: Rules, known: _Known | None = None) -> None:
    """Hash every entry Docker sends, depth first; an excluded folder is entered only if needed.

    In known mode only what the recipes read is looked at, and a `.git` folder
    inside it counts as its HEAD and the commit HEAD names (`_git_head`).
    """
    stack = [_Frame((), None, _sorted_entries(str(context), stream), 0, True)]
    while stack:
        frame = stack[-1]
        entry = next(frame.entries, None)
        if entry is None:
            stack.pop()
            continue
        if not frame.parts and entry.name.startswith(OWN_PREFIX):
            continue
        parts = (*frame.parts, entry.name)
        rel = "/".join(parts)
        if known is not None and not _on_a_read(rel, known.reads):
            continue
        decision = rules.decide(rel, frame.decision)
        is_junction = getattr(entry, "is_junction", None)
        if is_junction is not None and is_junction():
            raise _NoAnswer(f"{rel} is a junction")
        info = entry.stat(follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            if decision.excluded and not rules.may_admit_under(parts):
                continue
            if (
                known is not None
                and known.git_readers is not None
                and entry.name == ".git"
                and not decision.excluded
            ):
                _send_folders(stream, stack)
                if _git_head(stream, Path(entry.path), rel, known.git_readers):
                    continue
            listing = _sorted_entries(entry.path, stream)
            stack.append(_Frame(parts, decision, listing, stat.S_IMODE(info.st_mode), False))
            if not decision.excluded:
                _send_folders(stream, stack)
            continue
        if decision.excluded:
            continue
        _send_folders(stream, stack)
        if stat.S_ISLNK(info.st_mode):
            stream.put(b"l")
            stream.put(_utf8(rel))
            stream.put(oct(stat.S_IMODE(info.st_mode)).encode("ascii"))
            stream.put(_utf8(os.readlink(entry.path)))
        elif stat.S_ISREG(info.st_mode):
            stream.put(b"f")
            stream.put(_utf8(rel))
            mode, _ = stream.file(Path(entry.path))
            stream.put(oct(mode).encode("ascii"))
        else:
            raise _NoAnswer(f"{rel} is neither a file, a folder nor a symlink")


def _known(context: Path, found: Sequence[KnownRecipe | None]) -> _Known | None:
    """Known mode when every recipe the overlay names was read by hand; None walks it all."""
    recipes = [recipe for recipe in found if recipe is not None]
    if not recipes or len(recipes) != len(found):
        return None
    readers = sorted({recipe.git_reader for recipe in recipes})
    return _Known(
        reads=tuple(sorted({read for recipe in recipes for read in recipe.reads})),
        git_readers=(
            tuple(path for path, _ in readers) if _git_readers_unchanged(context, readers) else None
        ),
    )


def _fingerprint(
    context: Path,
    *,
    refs: Sequence[str],
    overlay: str,
    compose_files: Sequence[str],
    stream: _Stream,
) -> str:
    stream.put(FORMAT)
    stream.put(len(refs).to_bytes(8, "big"))
    for ref in sorted(refs):
        stream.put(_utf8(ref))
    _no_build_key_elsewhere(context, compose_files)
    stream.put(b"overlay")
    _, overlay_bytes = stream.file(context / overlay, keep=True)
    recipes = _overlay_recipes(overlay_bytes.decode("utf-8"), overlay)
    ignore = context / DOCKERIGNORE
    if ignore.exists() or ignore.is_symlink():
        stream.put(b"dockerignore")
        _, ignore_bytes = stream.file(ignore, keep=True)
        rules = _parse(ignore_bytes.decode("utf-8"))
    else:
        stream.put(b"no dockerignore")
        rules = Rules()
    found: list[KnownRecipe | None] = []
    for recipe in recipes:
        own_ignore = context / (recipe + DOCKERIGNORE)
        if own_ignore.exists() or own_ignore.is_symlink():
            raise _NoAnswer(f"{recipe}{DOCKERIGNORE} replaces the root .dockerignore")
        stream.put(b"dockerfile")
        stream.put(_utf8(recipe))
        _, recipe_bytes = stream.file(context / recipe, keep=True)
        found.append(KNOWN_RECIPES.get(hashlib.sha256(recipe_bytes).hexdigest()))
    known = _known(context, found)
    if known is not None:
        # Before the walk, so a known-mode stream can never equal a whole-walk one.
        stream.put(b"reads")
        stream.put(len(known.reads).to_bytes(8, "big"))
        for read in known.reads:
            stream.put(_utf8(read))
    stream.put(b"context")
    _walk(stream, context, rules, known)
    stream.put(b"end")
    return stream.hexdigest()


def fingerprint(
    context_dir: Path,
    *,
    refs: Sequence[str] = (),
    overlay: str = BUILD_FILE,
    compose_files: Sequence[str] = (BASE_FILE, OVERRIDE_FILE),
    max_bytes: int = MAX_BYTES,
    max_entries: int = MAX_ENTRIES,
) -> str | None:
    """64 hex characters naming what a build of `context_dir` is made from, or None for no answer.

    `context_dir` is the server folder, which is the build context of every
    family (`BUILD_CONTEXT` is "."). None means "could not read everything", and
    None never matches anything: the caller then does not keep the build.
    """
    try:
        return _fingerprint(
            Path(context_dir),
            refs=refs,
            overlay=overlay,
            compose_files=compose_files,
            stream=_Stream(max_bytes, max_entries),
        )
    except (OSError, UnicodeError, _NoAnswer) as exc:
        log.info("build fingerprint: no answer for %s: %s", context_dir, exc)
        return None
