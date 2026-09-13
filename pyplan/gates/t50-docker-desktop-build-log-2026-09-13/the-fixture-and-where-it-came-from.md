# T50 — the fixture, and its provenance

`_DOCKER_DESKTOP_TAIL` in `tests/test_docker.py` is **transcribed from the reporter's own
screenshot**, posted in a DM at 17:08 on 2026-09-13, and read on the owner's instruction.

It is not invented, and that is the whole point of writing this down: an invented
Docker-Desktop fixture would almost certainly have carried the `------` fences and the
`> [n/m] RUN` step header, because that is the shape `_buildkit_failure()` was written
against and the shape anyone reconstructing it from the code would reproduce. **The absence
of those lines IS the finding.** A fixture built from the code would have passed against the
broken code and proved nothing.

What the screenshot showed, in the app's own log pane:

```
… did not complete successfully: exit code: 1
[11:07:30]
[11:07:30]
[11:07:30] View build details: docker-desktop://dashboard/build/default/default/c6g4h689yvm5emfs0xe29iae7
[11:07:30]
```

And in the report line above it, the message the user actually read:

```
FAILED: InstallerError: the build failed (exit 1). Its last words were:
  ...MPILER_LAUNCHER="ccache" -DCMAKE_C_COMPILER_LAUNCHER="ccache" …
```

The leading `...` is what identified the branch: `last_words()` produces that prefix on its
final line only, which is reached only when `_buildkit_failure()` returned an empty block.

**He was provably on the build carrying T38's fix.** Module removal worked for him in the same
session ("removing is working confirmed"), and that fix exists only in `v0.8.66-fixtest`.

## Two facts about his failure that are NOT T50's to fix

Recorded here because they were measured from the same screenshots and will otherwise be lost:

1. **The build died at 41 seconds, at step #24** — earlier he reported ~15 minutes. 41 s is far
   too fast to be a compile; it is failing at or just after the cmake configure step. Whatever
   the earlier 15-minute failure was, this is a different one.
2. **A separate failure before it:** `ac-db-import exited 1, so its modules' SQL may be
   part-applied`, carrying AzerothCore's own `Could not update the World database` and
   `developer, please fix your sql query`. `may be part-applied` is the dangerous half — a
   half-migrated world database can break later runs by itself.
