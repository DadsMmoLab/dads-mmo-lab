"""The source patches an AzerothCore entry carries, read and written (T553, Unbound P2a).

The CMaNGOS family's `patch-sources` (`CmangosInstaller._patch_sources()` and the
three update-route hooks beside it), for the AzerothCore family: the same
`SourcePatch` data, the same tolerant `patch.apply()`, the same sentences. Kept in
its own module rather than in `azerothcore.py` so that file changes by a few
delegating lines.

The CMaNGOS family keeps its own copies of these bodies; folding both families
onto this module is a refactor of a family this ticket does not touch.

What the apply guarantees is `patch.py`'s, and it is what makes every caller here
safe to run more than once: a hunk already on disk is "already carries" and is
not written again, so a press after the install, a Rebuild and an update that
reset the checkout all end with one copy of each hunk.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path

from yulon.catalog.catalog import SourcePatch
from yulon.catalog.families import patch
from yulon.catalog.families.cmangos import CATALOG_ERROR_TAIL
from yulon.catalog.installer import InstallerError

NONE_CARRIED = "This server carries no source patches."


def patch_text(entry_name: str, installers_root: Path, spec: SourcePatch) -> str:
    """The patch file's text, or the catalog refusal for one this build does not ship."""
    path = installers_root / spec.file
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InstallerError(
            f"{entry_name}'s catalog names a source patch {spec.file} that this build does not "
            f"ship ({exc}). {CATALOG_ERROR_TAIL}"
        ) from exc


def loaded(
    entry_name: str, installers_root: Path, specs: Sequence[SourcePatch]
) -> list[tuple[SourcePatch, str]]:
    """Every carried patch with its text, read before any of them is applied."""
    return [(spec, patch_text(entry_name, installers_root, spec)) for spec in specs]


def resolve(
    spec: SourcePatch, text: str, server_dir: Path, *, dry_run: bool = False
) -> tuple[patch.FileResult, ...]:
    """`patch.apply()` inside the checkout `spec.source` names, its refusal as the sentence.

    `patch.PatchError`'s own sentence names the file and the line and says nothing
    was changed, so it is passed through as it stands.
    """
    try:
        return patch.apply(text, server_dir / spec.source, name=spec.file, dry_run=dry_run)
    except patch.PatchError as exc:
        raise InstallerError(str(exc)) from exc


def _where(spec: SourcePatch) -> str:
    """The checkout a patch edits, for a sentence: `.` is the server's own source."""
    return "the server's source" if spec.source == "." else spec.source


def would_change(patches: Sequence[tuple[SourcePatch, str]], server_dir: Path) -> bool:
    """Would applying these write any file? A dry resolution; refuses like the real one."""
    return any(
        result.applied
        for spec, text in patches
        for result in resolve(spec, text, server_dir, dry_run=True)
    )


def apply_lines(
    patches: Sequence[tuple[SourcePatch, str]], server_dir: Path, *, quiet: bool = False
) -> Iterator[str]:
    """Write every patch, one line per file; `quiet` says only what was written.

    Every patch is resolved dry first, so a second patch that no longer applies
    refuses before the first one has written a byte.
    """
    for spec, text in patches:
        resolve(spec, text, server_dir, dry_run=True)
    for spec, text in patches:
        if not quiet:
            yield f"Applying {spec.file} inside {_where(spec)}: {spec.reason}"
        for result in resolve(spec, text, server_dir):
            if result.applied and result.present:
                yield (
                    f"Patched {result.path} ({result.applied} of "
                    f"{result.applied + result.present} hunks; the rest were already there)."
                )
            elif result.applied:
                yield f"Patched {result.path}."
            elif not quiet:
                yield f"{result.path} already carries the fix in {spec.file}; leaving it."


def check_lines(patches: Sequence[tuple[SourcePatch, str]], server_dir: Path) -> Iterator[str]:
    """Resolve every patch against moved sources, writing nothing; say it either way."""
    if not patches:
        yield "This server carries no source patches, so there is none to check."
        return
    for spec, text in patches:
        yield f"Checking {spec.file} still applies to {_where(spec)} as it now stands."
        resolve(spec, text, server_dir, dry_run=True)
    yield "Every source patch this app carries still applies."


def written_paths(patches: Sequence[tuple[SourcePatch, str]]) -> dict[str, tuple[str, ...]]:
    """The paths each patch edits, per source `dest`: read out of the patch files themselves."""
    found: dict[str, tuple[str, ...]] = {}
    for spec, text in patches:
        try:
            hunks = patch.parse(text)
        except patch.PatchError:
            continue
        paths = tuple(dict.fromkeys(hunk.path for hunk in hunks))
        found[spec.source] = tuple(dict.fromkeys((*found.get(spec.source, ()), *paths)))
    return found
