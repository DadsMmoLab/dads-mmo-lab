"""One vocabulary for "what does this compose stack look like", from two sources.

`shape_from_plan()` reads the three files the engine renders (YAML, with compose's
`${VAR:-default}` interpolation resolved to the default, which is what compose does with no
`.env`). `shape_from_config()` reads `docker compose config --format json` from a real install.
Both produce `Service` records built from the fields the 2026-08-24 diff compared by hand
(`pyplan/checklist.md`, "The compose diff against the proven install"): image, container name,
ports, depends_on, restart, environment keys, volumes, build. `stack_from_plan()`/
`stack_from_config()` do the same for the top-level `name:`/`volumes:`/`networks:` blocks, which
no per-service record can see. `compare()` and `compare_stack()` then report every difference
that is not one of the documented design differences, so the fixture test and the live gate diff
read the same list.

WHY EACH RULE ERASES WHAT IT ERASES. A normaliser's rules are differences it will never report
again, so every one of them is here because the two sources legitimately disagree, and each is
paired in `test_compose_fixture.py` with a test that a meaningful change in the same area is
still caught:

* `${VAR:-default}` → `default`. The rendered files are pre-interpolation text; `compose config`
  is post-interpolation JSON. The default is what compose picks with no `.env`, which is the
  state both sides were captured in. The default itself is NOT erased: a template that starts
  publishing 13306 says so.
* image → its last path component. The registry prefix and the tag are a recorded design
  difference (`yulon.local/ac-wotlk-worldserver:native-<install id>` against upstream's
  `acore/ac-wotlk-worldserver:master`); the NAME is not, and `mysql` against `mariadb` is
  reported.
* named volume → `<named>` at its target. The volume names differ by design (`db-data` here,
  `ac-database` upstream — the collision fix, recorded-not-fixed). The KIND survives, so a bind
  where a managed volume belongs is still reported, and so does READ-ONLY: only the SELinux
  label characters are dropped from the mode column (below), never `ro`.
* bind source → relative to the install dir. It is an absolute host path that is `/home/pk/...`
  on the proven box and a pytest tmp dir here. Only paths actually under the install dir are
  stripped, so a sibling `/home/pk/srv-backup` stays absolute rather than becoming `./-backup`.
* mount mode → `ro` or `rw`, and nothing else. The label characters (`z`, `Z`) are this engine's
  SELinux suffix, which the proven install has no equivalent for; that is the whole of the
  justification, so it buys the removal of exactly those characters. `ro` is readable on both
  sides — `compose config` gives it its own `read_only` field — and a client-data mount that
  lost it is a real regression, so it is compared.
* environment → KEYS only. The values carry a per-install DB password and the proven box's own
  paths, and reproducing them is not this engine's job. Whether a key is present at all is.
* `depends_on` → sorted (service, condition) pairs. Compose does not preserve the mapping's
  order, and the short list form leaves the condition implicit at `service_started` — which is
  read out rather than blanked, because a weakened edge is exactly the kind of difference this
  fixture exists to find.
* a top-level `volumes:`/`networks:` declaration → its options UNDER ITS OWN KEY, with only
  compose's per-project `name:` value dropped (that one is per-install by construction:
  `compose config` writes `<project>_client-data` where the file says `client-data`). The key is
  kept, because a declaration carries options and options must stay attached to an identity — a
  `driver_opts.device` that moved from one 1.1 GB store to the other is otherwise invisible, and
  a problem line that does not name the volume cannot be acted on. The two names that genuinely
  differ between the sources are translated by `DESIGN_VOLUME_NAMES`, not erased.
* upstream's build-time env (`BUILD_TIME_ENV`, `BUILD_TIME_ENV_PREFIXES`) → forgiven when only
  the PROVEN install has it. Upstream's compose sets these on the runtime services and the
  image's entrypoint reads none of them (checked in the image, 2026-08-24), so the native stack
  drops them on purpose. The allowance is one-directional: the same key appearing only in the
  native stack is still reported, because that would mean the engine started writing them.

WHAT THIS VOCABULARY CANNOT SEE, so that "the diff is clean" is never read as "the two files
match". `Service` has no field for the image tag, an environment VALUE, `stop_grace_period`, the
healthcheck, `networks`, `user`, `tty`/`stdin_open`, `entrypoint`/`command` or the build context.
The first two are the cost of the rules above; the rest are fields this reduction does not carry.
Each is named by a test in `test_compose_fixture.py`, and where another test file already owns
the value it is named there too — `stop_grace_period`, the healthcheck and `user:` are asserted
on the rendered side by `test_composegen.py`. One pair is owned by no ASSERTION anywhere else
and so is asserted in `test_compose_fixture.py` itself: `ac-client-data-init`'s `entrypoint` and
`command`, the ~45-line resumable downloader that REPLACES upstream's un-resumable
`curl … > data.zip` and is the largest deliberate divergence in the stack. Its text IS in the
byte snapshot under `tests/data/wotlk-rendered/`, but a snapshot is a change-detector whose
documented remedy is regeneration — it says the file changed, never that the script must be able
to resume.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import yaml

from yulon.catalog.composegen import ComposePlan

_INTERPOLATION = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-([^}]*)\}")

_SELINUX_LABELS: frozenset[str] = frozenset({"z", "Z"})
"""The only mount-mode flags this vocabulary drops, and the only ones it is entitled to: they
are `platform.bind_label()`'s suffix, which the proven install has no equivalent for."""

BUILD_TIME_ENV: frozenset[str] = frozenset(
    {"AC_CCACHE", "CTYPE", "CSCRIPTS", "DATAPATH", "USER_CONF_PATH"}
)
"""Upstream's compose sets these on the runtime services; the image's entrypoint reads none of
them (checked in the image, 2026-08-24). The native stack drops them on purpose."""

BUILD_TIME_ENV_PREFIXES: tuple[str, ...] = ("AC_RESTARTER_",)
"""The three empty `AC_RESTARTER_*` keys, same reasoning, matched by prefix."""

NO_ALIASES: Mapping[str, str] = {}
"""No name is a recorded design difference here. Networks use this: `ac-network` is `ac-network`
on both sides, so a renamed network is a difference and not an allowance."""

DESIGN_VOLUME_NAMES: Mapping[str, str] = {
    "db-data": "ac-database",
    "client-data": "ac-client-data",
}
"""The one recorded volume-name difference, mapped native -> upstream.

`pyplan/checklist.md`, "Recorded, not fixed" (2026-08-24): "the volume names differ (`db-data`
and `client-data` vs `ac-database` and `ac-client-data`). Both are project-scoped so nothing
collides". The 2026-08-23 teardown gate saw the proven install's two as
`wow-server-playerbots_ac-database` and `_ac-client-data`, which is the same two short names.

A MOUNT carries nothing but a name, a kind and a target, so there the name is erased outright. A
top-level DECLARATION also carries options, and options compared without an identity to hang on
cannot tell "client-data moved to another disk" from "db-data did". So the two recorded names
are translated here instead, and every other name is compared exactly as written — a third
volume, or a rename nobody recorded, is a difference."""

NATIVE_ONLY_ENV: frozenset[tuple[str, str]] = frozenset(
    {("ac-db-import", "AC_PLAYERBOTS_DATABASE_INFO")}
)
"""(service, key) the native stack carries and the proven script install lacks: the importer
is told about the playerbots schema, which the repair gate recorded as missing upstream. The
service is half the key on purpose — the same variable on another service is a different fact."""

_COMPARED_FIELDS: tuple[str, ...] = (
    "container_name",
    "image",
    "ports",
    "volumes",
    "depends_on",
    "build",
    "restart",
)
"""Compared verbatim; `env_keys` has its own allow-listed comparison."""


@dataclass(frozen=True)
class Service:
    """One compose service reduced to the fields the two sources can honestly share."""

    container_name: str | None
    image: str | None
    ports: frozenset[tuple[str, int, str]]
    volumes: frozenset[tuple[str, str, str, str]]
    env_keys: frozenset[str]
    depends_on: tuple[tuple[str, str], ...]
    build: tuple[str, str] | None
    restart: str | None


@dataclass(frozen=True)
class Stack:
    """A compose document's top level — everything `dict[str, Service]` has no room for.

    `project` is the `name:` key. It is NOT diffed between the two sources: it carries the
    per-install id here, and E.2 strips it from the captured fixture precisely so the fixture
    does not name the box it came from. What IS checked is that the native stack has one at all,
    because that name is what keys every named volume, and it is the only reason erasing volume
    names is safe rather than a way for two installs to share one database.
    """

    project: str | None
    volumes: tuple[tuple[str, tuple[str, ...]], ...]
    networks: tuple[tuple[str, tuple[str, ...]], ...]


def resolve_defaults(text: str) -> str:
    """`${VAR:-default}` becomes `default`, which is what compose renders with an empty `.env`.

    A reference with no default is left as it is written: an unresolvable variable must not
    quietly become the empty string and compare equal to whatever the other side published.
    """
    return _INTERPOLATION.sub(lambda m: m.group(1), text)


def image_name(ref: str) -> str:
    """`registry/prefix-name:tag` -> `prefix-name`; the prefix and tag are the design difference.

    This also erases a base image's version (`mysql:8.4` and `mysql:5.7` are one name here);
    `test_catalog.py` is where the pinned database image is defended.
    """
    return ref.rsplit(":", 1)[0].split("/")[-1]


def port_from_string(spec: str) -> tuple[str, int, str]:
    """A short-form port (`[host_ip:]published:target[/proto]`) as (published, target, protocol).

    The host IP is dropped because the two sources spell it differently — the SOAP mapping
    carries its loopback prefix inside the `${...:-127.0.0.1:7878}` default here and arrives as
    a separate `host_ip` field from `compose config` — so it cannot be compared without
    reporting a difference that is not one. The protocol is read rather than dropped: it varies
    with no machine, and `3724:3724/udp` is a realm nobody can log in to.
    """
    resolved, _, proto = resolve_defaults(spec).partition("/")
    parts = resolved.split(":")
    return parts[-2], int(parts[-1]), proto or "tcp"


def port_from_config(entry: dict[str, Any]) -> tuple[str, int, str]:
    """A `compose config` port object as (published, target, protocol)."""
    return str(entry["published"]), int(entry["target"]), str(entry.get("protocol") or "tcp")


def _mount_mode(columns: list[str]) -> str:
    """A short form's mode column(s) reduced to `ro` or `rw`.

    Everything dropped here is dropped for one stated reason: the SELinux label characters are
    this engine's `{{BIND_LABEL}}` suffix and the proven install has no equivalent. `ro` is not
    a label, is readable on both sides, and stays. A flag that is neither raises rather than
    being silently erased — an unrecognised mode is a decision, not a default.
    """
    flags = {flag for column in columns for flag in column.split(",") if flag}
    unexplained = flags - _SELINUX_LABELS - {"ro", "rw"}
    if unexplained:
        raise ValueError(f"unrecognised mount mode {sorted(unexplained)}: decide before erasing")
    return "ro" if "ro" in flags else "rw"


def volume_from_string(spec: str) -> tuple[str, str, str, str]:
    """A short-form volume (`source:target[:mode]`) as (type, source, target, mode)."""
    columns = spec.split(":")
    source, target = columns[0], columns[1].rstrip("/")
    mode = _mount_mode(columns[2:])
    if source.startswith((".", "/", "~")):
        return "bind", source, target, mode
    return "volume", "<named>", target, mode


def volume_from_config(entry: dict[str, Any], *, root: str | None) -> tuple[str, str, str, str]:
    """A `compose config` volume object as (type, source, target, mode); binds relative to `root`.

    `root` is stripped only from a path that IS the install dir or lies under it — a plain
    `startswith` would rewrite a sibling `/home/pk/srv-backup` mount into this install's own
    `./-backup` and hide a mount of the wrong tree. Read-only arrives as its own field here and
    in the mode column on the other side; both end up as the same `ro`.
    """
    kind = str(entry["type"])
    target = str(entry["target"]).rstrip("/")
    mode = "ro" if entry.get("read_only") else "rw"
    if kind == "volume":
        return "volume", "<named>", target, mode
    source = str(entry["source"])
    base = root.rstrip("/") if root else None
    if base and (source == base or source.startswith(f"{base}/")):
        source = f".{source[len(base):]}"
    return kind, source or ".", target, mode


def _depends(raw: Any) -> tuple[tuple[str, str], ...]:
    """`depends_on` in either spelling; the list form's implicit condition is written out."""
    if not raw:
        return ()
    if isinstance(raw, list):
        return tuple(sorted((str(name), "service_started") for name in raw))
    return tuple(sorted((str(name), str(cond.get("condition", ""))) for name, cond in raw.items()))


def _env_keys(raw: Any) -> frozenset[str]:
    """The keys of an `environment:` block, written as a mapping or as `KEY=value` items."""
    if not raw:
        return frozenset()
    if isinstance(raw, list):
        return frozenset(str(item).split("=", 1)[0] for item in raw)
    return frozenset(str(key) for key in raw)


def _build(raw: Any) -> tuple[str, str] | None:
    """(dockerfile, target). The context is not compared: it is `.` here and the proven box's
    own absolute path there, and it is the same directory in both cases."""
    if not raw:
        return None
    return str(raw.get("dockerfile", "Dockerfile")), str(raw.get("target", ""))


def _service(svc: dict[str, Any], *, root: str | None, from_config: bool) -> Service:
    ports = svc.get("ports") or []
    volumes = svc.get("volumes") or []
    return Service(
        container_name=svc.get("container_name"),
        image=image_name(str(svc["image"])) if svc.get("image") else None,
        ports=frozenset(
            port_from_config(p) if from_config else port_from_string(str(p)) for p in ports
        ),
        volumes=frozenset(
            volume_from_config(v, root=root) if from_config else volume_from_string(str(v))
            for v in volumes
        ),
        env_keys=_env_keys(svc.get("environment")),
        depends_on=_depends(svc.get("depends_on")),
        build=_build(svc.get("build")),
        restart=svc.get("restart"),
    )


def _options(body: Any) -> tuple[str, ...]:
    """One top-level declaration's options, flattened and sorted.

    `name` is dropped: the declaration's own key names it here, and `compose config` fills in
    the project-prefixed `<project>_client-data` there, which is per-install by construction.
    Every other key — `driver`, `driver_opts`, `external`, `labels` — is kept.
    """
    if not body:
        return ()
    flat: list[str] = []
    for key, value in body.items():
        if key == "name":
            continue
        if isinstance(value, dict):
            flat.extend(f"{key}.{inner}={value[inner]}" for inner in value)
        else:
            flat.append(f"{key}={value}")
    return tuple(sorted(flat))


def _declarations(
    raw: Any, *, aliases: Mapping[str, str]
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """A whole top-level `volumes:`/`networks:` block as sorted (name, options) pairs.

    The name is kept — `compose config` keys this map by the SHORT name on both sides, and only
    the `name:` INSIDE carries the project prefix — so that a difference can be attributed to
    one declaration and reported by name. `aliases` translates the recorded design renames.
    """
    if not raw:
        return ()
    return tuple(
        sorted((aliases.get(str(name), str(name)), _options(body)) for name, body in raw.items())
    )


def shape_from_plan(plan: ComposePlan) -> dict[str, Service]:
    """The three rendered files merged the way compose merges them, then reduced.

    `ports` and `volumes` are CONCATENATED across files and `environment` mappings are merged
    key by key, which is what compose does; every other key is last-file-wins.
    """
    merged: dict[str, dict[str, Any]] = {}
    for text in (plan.base, plan.override, plan.build):
        doc = yaml.safe_load(resolve_defaults(text)) or {}
        for name, svc in (doc.get("services") or {}).items():
            target = merged.setdefault(name, {})
            for key, value in svc.items():
                if key in ("volumes", "ports") and isinstance(value, list):
                    target[key] = list(target.get(key, [])) + value
                elif key == "environment" and isinstance(value, dict):
                    target[key] = {**target.get(key, {}), **value}
                else:
                    target[key] = value
    return {name: _service(svc, root=None, from_config=False) for name, svc in merged.items()}


def shape_from_config(data: dict[str, Any], *, root: str | None = None) -> dict[str, Service]:
    """`docker compose config --format json` reduced; `root` is the install dir to relativise."""
    return {
        name: _service(svc, root=root, from_config=True)
        for name, svc in (data.get("services") or {}).items()
    }


def stack_from_plan(plan: ComposePlan) -> Stack:
    """The three rendered files' top level, merged.

    The text is read WITHOUT `resolve_defaults()`: nothing up here is interpolated, and that
    pass would mangle the `$${var:-}` shell spellings inside a service's inline script.
    """
    project: str | None = None
    volumes: dict[str, Any] = {}
    networks: dict[str, Any] = {}
    for text in (plan.base, plan.override, plan.build):
        doc = yaml.safe_load(text) or {}
        project = str(doc["name"]) if doc.get("name") else project
        volumes.update(doc.get("volumes") or {})
        networks.update(doc.get("networks") or {})
    return Stack(
        project,
        _declarations(volumes, aliases=DESIGN_VOLUME_NAMES),
        _declarations(networks, aliases=NO_ALIASES),
    )


def stack_from_config(data: dict[str, Any], *, root: str | None = None) -> Stack:
    """`docker compose config --format json`'s top level. `root` is accepted for symmetry with
    `shape_from_config()` and unused: nothing up here is a host path."""
    del root
    return Stack(
        str(data["name"]) if data.get("name") else None,
        _declarations(data.get("volumes"), aliases=DESIGN_VOLUME_NAMES),
        _declarations(data.get("networks"), aliases=NO_ALIASES),
    )


def _is_build_time(key: str) -> bool:
    return key in BUILD_TIME_ENV or key.startswith(BUILD_TIME_ENV_PREFIXES)


def _show(value: object) -> str:
    """A frozenset prints as a plain set, its members SORTED.

    Set repr order varies between processes, and the E.3/E.4 gates paste these lines into a
    record: a difference must be reported the same way twice or the record cannot be diffed.
    """
    if isinstance(value, frozenset):
        return ("{" + ", ".join(repr(item) for item in sorted(value)) + "}") if value else "set()"
    return str(value)


def compare(native: dict[str, Service], proven: dict[str, Service]) -> list[str]:
    """Every difference that is not a documented design difference; empty means "matches".

    Service level only. `compare_stack()` covers the top-level blocks, and the two are meant to
    be run together — a clean list from this one alone does not say the documents match.
    """
    problems: list[str] = []
    if set(native) != set(proven):
        problems.append(f"services: {_show(frozenset(native))} vs {_show(frozenset(proven))}")
    for name in sorted(set(native) & set(proven)):
        ours, theirs = native[name], proven[name]
        for field in _COMPARED_FIELDS:
            mine, its = getattr(ours, field), getattr(theirs, field)
            if mine != its:
                problems.append(f"{name}: {field} {_show(mine)} vs {_show(its)}")
        extra = {k for k in ours.env_keys - theirs.env_keys if (name, k) not in NATIVE_ONLY_ENV}
        missing = {k for k in theirs.env_keys - ours.env_keys if not _is_build_time(k)}
        if extra:
            problems.append(f"{name}: env keys only in the native stack {sorted(extra)}")
        if missing:
            problems.append(f"{name}: env keys only in the proven install {sorted(missing)}")
    return problems


def _declaration_problems(
    label: str,
    native: tuple[tuple[str, tuple[str, ...]], ...],
    proven: tuple[tuple[str, tuple[str, ...]], ...],
) -> list[str]:
    """One line per declaration that differs, each naming the declaration.

    A gate operator reading `volumes: [(), ('driver_opts.device=…',)] vs [(), ()]` cannot tell
    which of two 1.1 GB stores moved. Naming it is the whole reason the key is kept.
    """
    ours, theirs = dict(native), dict(proven)
    problems: list[str] = []
    if only_ours := sorted(set(ours) - set(theirs)):
        problems.append(f"{label}: only in the native stack {only_ours}")
    if only_theirs := sorted(set(theirs) - set(ours)):
        problems.append(f"{label}: only in the proven install {only_theirs}")
    for name in sorted(set(ours) & set(theirs)):
        if ours[name] != theirs[name]:
            problems.append(f"{label}: {name} {list(ours[name])} vs {list(theirs[name])}")
    return problems


def compare_stack(native: Stack, proven: Stack) -> list[str]:
    """The top-level blocks `compare()` has no room for; empty means "matches"."""
    problems: list[str] = []
    if not native.project:
        problems.append(
            "project: the native stack has no `name:`, so its named volumes are keyed by the "
            "install directory and two installs can share one database"
        )
    problems.extend(_declaration_problems("volumes", native.volumes, proven.volumes))
    problems.extend(_declaration_problems("networks", native.networks, proven.networks))
    return problems
