"""The answers a player gave to a module's questions, kept per install (T104).

Until T104 no answer was kept anywhere: `Applier._values()` filled every prompt
from the manifest's default, so the Update dialog pre-selected the DEFAULT and
not what the player had picked. The T100 cold review measured it on
Hearthstone Tweaks -- installed with 5 minutes, Update + OK, back to 30. The
owner's decision of 2026-09-24 was "ask all, remember answers".

**Where it lives, and why there.** `<server_dir>/.yulon-module-answers.json`,
beside `.yulon-install.json` and `.yulon-network.json`, for the reasons
`networking.INTENT_FILE` gives for the second of those: a person's choice about
ONE install belongs with that install, and it is not a run's progress, so it
must not live inside the file the install engine rewrites whole after every
stage. It is not a field of `state.json` either: `AppState` is
`extra="forbid"`, so an older build reading a newer `state.json` would call it
corrupt and move it aside, forgetting every install it knew.

**Keyed `<type>/<id>`**, because a module id is only unique within its family
(`controller_view.selected_manifest()` looks manifests up by both for that
reason).

Reading is deliberately forgiving and writing deliberately careful. Any shape
this build cannot use reads as "nothing saved" -- the caller then offers the
manifest's defaults, which is exactly what every install made before T104 gets.
A write is atomic (temp + `os.replace`), so a half-written record is never read
as a record.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

from yulon.log import get_logger
from yulon.manifest import Manifest

logger = get_logger(__name__)

ANSWERS_FILE = ".yulon-module-answers.json"
"""The record's name in the server folder. One of `catalog.native.OUR_OWN_FILES`."""

SCHEMA_VERSION = 1


def _key(manifest: Manifest) -> str:
    return f"{manifest.type}/{manifest.id}"


def _read_all(server_dir: Path) -> dict[str, dict[str, str]]:
    """Every module's saved answers, or `{}` for any file this build cannot use."""
    path = server_dir / ANSWERS_FILE
    try:
        with path.open(encoding="utf-8-sig") as fh:
            parsed = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.debug(f"no usable module answers in {server_dir}: {exc}")
        return {}
    modules = parsed.get("modules") if isinstance(parsed, dict) else None
    if not isinstance(modules, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for key, answers in modules.items():
        if isinstance(key, str) and isinstance(answers, dict):
            out[key] = {
                k: v for k, v in answers.items() if isinstance(k, str) and isinstance(v, str)
            }
    return out


def read_answers(server_dir: Path, manifest: Manifest) -> dict[str, str]:
    """What was last answered for `manifest` on the install at `server_dir`, unchecked.

    Raw strings: whether each is still a usable answer to the manifest's
    question (a `choice` whose option was dropped, say) is
    `Applier.remembered_answers()`'s call, because it is `apply.check_answer()`'s.
    """
    return dict(_read_all(server_dir).get(_key(manifest), {}))


def record_answers(server_dir: Path, manifest: Manifest, answers: Mapping[str, str]) -> str:
    """Remember `answers` for `manifest` on this install; `""` if written, else why not.

    MERGED over what was saved for the module before, so an answer to a
    question this press did not ask (a configure-only one, say) is kept.
    Other modules' answers are kept as they were.

    It answers instead of raising, as `networking.record_network_intent()` does:
    the install it follows has already happened, and an unwritable folder means
    the next Update shows defaults -- worth a line in the report, not an
    exception over a successful install.
    """
    everything = _read_all(server_dir)
    merged = dict(everything.get(_key(manifest), {}))
    merged.update({str(k): str(v) for k, v in answers.items()})
    everything[_key(manifest)] = merged
    payload = {"schema_version": SCHEMA_VERSION, "modules": everything}
    path = server_dir / ANSWERS_FILE
    tmp = path.with_name(path.name + ".new")
    try:
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)  # atomic on POSIX and on Windows
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        logger.warning(f"could not remember the answers for {_key(manifest)} in {path}: {exc}")
        return str(exc)
    return ""
