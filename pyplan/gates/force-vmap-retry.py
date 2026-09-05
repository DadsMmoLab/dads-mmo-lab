"""7.5's forced vmap retry, driven through the seam that already exists.

WHAT THIS CLOSES. 7.5 asks for "a full install with the 1.12.1 client incl. a
forced vmap retry". The retry recipe has never fired. Bug §37 established why
the natural route is closed: `Segmentation fault (core dumped)` is printed by a
SHELL reaping a child, these tools are exec'd as the container's PID 1 with no
shell in between, and a signal-killed container writes zero bytes -- so a recipe
matching only on log text could not see the failure it names. That was fixed by
`when_returncode_in`, and the fix has unit tests and one stage-level test, but
nothing has ever driven it against real containers and a real client.

WHY THERE IS NO PRODUCTION CHANGE HERE. An audit pass suggested adding a harness
override to `extract.py` -- an environment variable that makes the first
extractor exit non-zero. That would put a switch in shipped code whose only
purpose is to break a shipped install, and there is no need: `Seams.run_container`
is already injectable, and the extract and mmaps stages take it from there
(`families/cmangos.py:498,570`). So the crash is injected at the seam, and every
other byte of the run is the real engine, the real plan, the real containers and
the real client.

WHAT IS AND IS NOT FAKED, stated plainly so the record cannot overclaim. The
first `vmap extract` container really runs, on the real client, and really
produces its files; what this wrapper substitutes is the STATUS it reports --
139, and an empty tail, which is exactly what `docker.run_container` hands back
for a signal-killed PID 1. Everything downstream is untouched: `_retry_matches`
sees a status it recognises, the recipe re-runs both named tools for real, and
`_conclude` counts real files. What this does NOT prove is that a CMaNGOS
extractor can segfault on this client -- nobody has made one do that, and §37
says nobody should expect to.

RUN IT:
    python force-vmap-retry.py <server-dir> <client-dir>

Expected transcript, and the gate is these four lines in this order:
    vmap extract: running /opt/mangos/bin/tools/vmap_extractor ...
    vmap extract crashed the way the retry recipe expects; running vmap extract,
        vmap assemble again once
    vmap extract: retrying ...
    vmap assemble: retrying ...
"""

from __future__ import annotations

import sys
from pathlib import Path

from yulon import docker, platform
from yulon.catalog import native
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families import family_for
from yulon.catalog.installer import InstallOptions, InstallerError
from yulon.install_wiring import _terminal_prompter, import_gate_for
from yulon.log import get_logger, use_utf8_streams

use_utf8_streams()
logger = get_logger(__name__)

CRASH_ONCE_ON = "vmap_extractor"
CRASHED_RETURNCODE = 139
"""128 + SIGSEGV, the status `wow-vanilla`'s shipped recipe names."""


def main() -> int:
    server_dir, client_dir = Path(sys.argv[1]), Path(sys.argv[2])
    entry = load_catalog().get("wow-vanilla")

    # Named `block`, not `native`: `yulon.catalog.native` is imported above for
    # `native.Seams`, and a local of the same name shadows it four lines before
    # it is used. Caught by reading, not by running -- the failure would have
    # been an AttributeError twenty minutes into a real install.
    block = entry.install.native
    assert block is not None and block.cmangos is not None
    recipe = block.cmangos.extract.retry
    assert recipe is not None, "wow-vanilla ships no retry recipe; there is nothing to force"
    assert CRASHED_RETURNCODE in recipe.when_returncode_in, (
        f"the shipped recipe names {recipe.when_returncode_in}, not {CRASHED_RETURNCODE}; "
        "forcing a status the catalog does not name would prove nothing"
    )
    print(f"shipped recipe: statuses={recipe.when_returncode_in} tools={recipe.tools}", flush=True)

    real = docker.run_container
    crashed: list[str] = []

    def crash_first_extract(spec: docker.ContainerRun, **kwargs: object) -> docker.AttachedRun:
        """Run it for real, then report a signal death the first time only."""
        run = real(spec, **kwargs)  # type: ignore[arg-type]
        program = spec.argv[0] if spec.argv else ""
        if CRASH_ONCE_ON in program and not crashed:
            crashed.append(program)
            print(f"[harness] reporting {CRASHED_RETURNCODE} for {program}", flush=True)
            # An empty tail on purpose: a signal-killed PID 1 writes nothing,
            # which is the whole reason `when_returncode_in` had to exist.
            return docker.AttachedRun(CRASHED_RETURNCODE, ())
        return run

    # NOT `installer_for_app()`. That path reaches `installer_for()`, which builds
    # its own `native.Seams(platform_id=platform_id)` at `installer.py:431` and
    # takes no seam overrides -- so a `run_container=` handed to it would be
    # silently ignored and this harness would report a clean install as a forced
    # retry. The family engine is constructed directly instead, which is the same
    # object `installer_for()` would have returned, with the one seam replaced.
    probe, reset = import_gate_for(entry)
    engine = family_for(entry)(
        entry,
        import_probe=probe,
        reset_unfinished=reset,
        seams=native.Seams(
            platform_id=platform.detect,
            run_container=crash_first_extract,
        ),
    )
    options = InstallOptions(server_dir=server_dir, client_dir=client_dir)
    try:
        for line in engine.run(options, ask=_terminal_prompter):
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
    except InstallerError as exc:
        sys.stderr.write(f"install failed: {exc}\n")
        return 1
    if not crashed:
        sys.stderr.write(
            "NOT A GATE: the extractor was never reached, so no crash was injected and the "
            "retry was never put to the question.\n"
        )
        return 1
    print("INSTALL RETURNED CLEANLY after a forced retry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
