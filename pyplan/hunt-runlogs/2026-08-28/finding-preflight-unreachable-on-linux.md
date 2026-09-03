# The whole preflight module is unreachable on Linux — 2026-08-28

**This is one root cause behind two separately-found defects.** Both were found independently, on
different boxes, by agents that did not know about each other's result.

## The call graph
- `preflight.gather()` has exactly **one** non-test caller: `yulon/catalog/native.py:272`
  (`gather: Callable[..., preflight.Facts] = preflight.gather`).
- `installer_for()` (`yulon/catalog/installer.py:697-738`) returns `native.NativeInstaller` only when
  `entry.install.is_native(here)`; otherwise the bash-script `Installer`.
- `wow-wotlk` declares `script_platforms: ["linux"]`, so on **every** Linux box `installer_for()`
  returns the bash `Installer`.
- `Installer.preflight()` / `Installer.run()` contain zero references to `preflight` or
  `bind_mount_ok`.

So on Linux — which is where every current user is — **nothing in `catalog/preflight.py` runs.**

## Defect 1: the SELinux bind probe (found on yulon-fedora)
`docker.py:2428` (`bind_mount_ok`) mounts `{mount}:/probe:ro` with no `:z`/`:Z`. Proven denied on
enforcing SELinux by A/B `docker run`. Its only production caller is `preflight.py:183`
(`_default_bind_probe`). Harmless today only because the path is dead; HIGH for macOS and native
Windows, the native engine's actual targets.

## Defect 2: the CPU-vs-RAM check (found on yulon-arch)
`preflight.py:338-359` (`_cpu_check`) already implements exactly the right rule:

    jobs = facts.vm.cpus + 1
    affordable = int(facts.vm.memory_bytes / GIB // 2)
    if jobs > affordable and affordable >= 1: warn(...)

and its docstring already records the reason a knob cannot exist: *"Upstream's Dockerfile hardcodes
`-j $(nproc+1)` INSIDE the RUN, so no build argument can change the job count."* Confirmed live on
Arch — `ps aux` showed `cmake --build . -j 9` on an 8-vCPU box, from
`apps/docker/Dockerfile:105` in the cloned AzerothCore tree.

Because the check never runs on Linux, **yulon-fedora walked into `-j7` at 8 GB with no warning and
swap-thrashed itself unreachable at 83% of the build.** The check would have caught it: at 8 GB,
`affordable = 4` against `jobs = 7`.

Second-order problem even if it did run: the remedy text says *"set Docker Desktop to N CPUs"*.
There is no Docker Desktop on native Linux, so the advice would be wrong there.

## Why this matters more than either half
The two defects look unrelated — one is SELinux labelling, one is build parallelism. They are the
same bug: a preflight module that is written, tested and correct, and that no Linux user ever
reaches. Phase 7.1/7.2 move Linux onto the native engine, at which point **both** wake up at once —
one as a hard `refuse` on every Fedora/RHEL install, the other as advice naming a product that is
not installed.

Related, from [[server-and-build-memory-budget]]: more cores without more RAM makes an OOM *more*
likely. `yulon-use.ps1`'s single-VM tier (12 vCPU / 24 GB) gives `jobs = 13` against
`affordable = 12` and trips this very warning; the two-VM tier (8 vCPU / 23 GB) is the healthier
ratio.
