"""One vocabulary for "what does this compose stack look like", from two sources.

`shape_from_plan()` reads the three files the engine renders (YAML, with compose's
`${VAR:-default}` interpolation resolved to the default, which is what compose does with no
`.env`). `shape_from_config()` reads `docker compose config --format json` from a real install.
Both produce `Service` records built from the fields the 2026-08-24 diff compared by hand
(`pyplan/checklist.md`, "The compose diff against the proven install"): image, container name,
ports, depends_on, restart, environment keys, volumes, build. `compare()` then reports every
difference that is not one of the documented design differences, so the fixture test and the
live gate diff read the same list.

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
  where a managed volume belongs is still reported.
* bind source → relative to the install dir. It is an absolute host path that is `/home/pk/...`
  on the proven box and a pytest tmp dir here. Only paths actually under the install dir are
  stripped, so a sibling `/home/pk/srv-backup` stays absolute rather than becoming `./-backup`.
* environment → KEYS only. The values carry a per-install DB password and the proven box's own
  paths, and reproducing them is not this engine's job. Whether a key is present at all is.
* `depends_on` → sorted (service, condition) pairs. Compose does not preserve the mapping's
  order, and the short list form leaves the condition implicit at `service_started` — which is
  read out rather than blanked, because a weakened edge is exactly the kind of difference this
  fixture exists to find.
* upstream's build-time env (`BUILD_TIME_ENV`, `BUILD_TIME_ENV_PREFIXES`) → forgiven when only
  the PROVEN install has it. Upstream's compose sets these on the runtime services and the
  image's entrypoint reads none of them (checked in the image, 2026-08-24), so the native stack
  drops them on purpose. The allowance is one-directional: the same key appearing only in the
  native stack is still reported, because that would mean the engine started writing them.

WHAT THIS VOCABULARY CANNOT SEE, so that "the diff is clean" is never read as "the two files
match". `Service` has no field for the image tag, the mount mode, an environment VALUE,
`stop_grace_period`, the healthcheck, `networks`, `tty`/`stdin_open`, `entrypoint`/`command` or
the build context. The first four are the cost of the rules above; the rest are fields the
2026-08-24 hand diff covered and this reduction does not. Each has a test in
`test_compose_fixture.py` naming it, and the value-level assertions live in
`test_composegen.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml

from yulon.catalog.composegen import ComposePlan

_INTERPOLATION = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-([^}]*)\}")

BUILD_TIME_ENV: frozenset[str] = frozenset(
    {"AC_CCACHE", "CTYPE", "CSCRIPTS", "DATAPATH", "USER_CONF_PATH"}
)
"""Upstream's compose sets these on the runtime services; the image's entrypoint reads none of
them (checked in the image, 2026-08-24). The native stack drops them on purpose."""

BUILD_TIME_ENV_PREFIXES: tuple[str, ...] = ("AC_RESTARTER_",)
"""The three empty `AC_RESTARTER_*` keys, same reasoning, matched by prefix."""

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
    ports: frozenset[tuple[str, int]]
    volumes: frozenset[tuple[str, str, str]]
    env_keys: frozenset[str]
    depends_on: tuple[tuple[str, str], ...]
    build: tuple[str, str] | None
    restart: str | None


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


def port_from_string(spec: str) -> tuple[str, int]:
    """A compose short-form port (`[host_ip:]published:target[/proto]`) as (published, target).

    The host IP is dropped because the two sources spell it differently — the SOAP mapping
    carries its loopback prefix inside the `${...:-127.0.0.1:7878}` default here and arrives as
    a separate `host_ip` field from `compose config` — so it cannot be compared without
    reporting a difference that is not one.
    """
    resolved = resolve_defaults(spec).split("/")[0]
    parts = resolved.split(":")
    return parts[-2], int(parts[-1])


def port_from_config(entry: dict[str, Any]) -> tuple[str, int]:
    """A `compose config` port object as (published, target)."""
    return str(entry["published"]), int(entry["target"])


def volume_from_string(spec: str) -> tuple[str, str, str]:
    """A short-form volume (`source:target[:mode]`) as (type, source, target).

    The mode column is dropped: it is where this engine appends the SELinux `:z` label the
    proven install has no equivalent for, and `compose config` reports read-only as its own
    field rather than in that column.
    """
    source, target = spec.split(":")[:2]
    target = target.rstrip("/")
    if source.startswith((".", "/", "~")):
        return "bind", source, target
    return "volume", "<named>", target


def volume_from_config(entry: dict[str, Any], *, root: str | None) -> tuple[str, str, str]:
    """A `compose config` volume object as (type, source, target); binds relative to `root`.

    `root` is stripped only from a path that IS the install dir or lies under it — a plain
    `startswith` would rewrite a sibling `/home/pk/srv-backup` mount into this install's own
    `./-backup` and hide a mount of the wrong tree.
    """
    kind = str(entry["type"])
    target = str(entry["target"]).rstrip("/")
    if kind == "volume":
        return "volume", "<named>", target
    source = str(entry["source"])
    base = root.rstrip("/") if root else None
    if base and (source == base or source.startswith(f"{base}/")):
        source = f".{source[len(base):]}"
    return kind, source or ".", target


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


def _is_build_time(key: str) -> bool:
    return key in BUILD_TIME_ENV or key.startswith(BUILD_TIME_ENV_PREFIXES)


def _show(value: object) -> str:
    """A frozenset prints as a plain set so a problem line reads `{...} vs set()`."""
    return str(set(value)) if isinstance(value, frozenset) else str(value)


def compare(native: dict[str, Service], proven: dict[str, Service]) -> list[str]:
    """Every difference that is not a documented design difference; empty means "matches"."""
    problems: list[str] = []
    if set(native) != set(proven):
        problems.append(f"services: {set(native)} vs {set(proven)}")
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
