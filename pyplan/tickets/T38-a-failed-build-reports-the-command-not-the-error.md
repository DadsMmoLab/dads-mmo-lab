# T38 — a failed build reports the command that failed, never the error that failed it

**Status:** OPEN — reviewed (Codex: REJECT, four findings taken in one round) and green; owes a gate and a reply to Lac
**Filed:** 2026-09-12 by the lead, from Lac in `#tech-support-live` (14:28) and `#-yulon` (18:29), with Baerthe's "yeah I think our module system is still a bit buggy" (18:29) and the owner's "Ill look into it, I havent tested the module system enough" (19:20).
**Hand:** the lead. Unit only for the fix; the replication needed a daemon and ran on `m910q`.

## The report

Lac, adding a module and rebuilding:

> I am getting an error when trying to add modules and rebuild server with Yulon is there somethine I need to do different?

```
FAILED: InstallerError: the build failed (exit 1). Its
last words were: ..."
-DCMAKE_C_COMPILER=\"clang\"
-DCMAKE_CXX_COMPILER_LAUNCHER=\"ccache\"
-DCMAKE_C_COMPILER_LAUNCHER=\"ccache\"
-DBoost_USE_STATIC_LIBS=\"ON\"
&& cmake --build . --config \"$CTYPE\" -j $(($(nproc) + 1))    && cmake --install . --config
\"$CTYPE\"" did not complete successfully: exit code: 1
```

The panel said 100% and 0:00:55. Later, in `#-yulon`: "I can't get any modules to work, I have tried transmog and lootpet."

**Nothing in that message says what went wrong.** It is the `cmake` command line, echoed back. Lac cannot act on it, and neither can we — which is why the module bug underneath it is still undiagnosed.

## Root cause (replicated)

`native.py:5259` reports a failed stage as `f"{what} failed (exit {run.returncode}). Its last words were: {docker.last_words(run.tail)}"`, and `docker.last_words()` (`docker.py:3126`) takes the last five non-blank lines of the tail.

On a failed `docker build` those five lines are always BuildKit's epilogue, never the error. Replicated on `m910q` (Docker 29.7.2) with a busybox stand-in that fails in three seconds — the tool's behaviour is the question, so the payload does not need to be a 40-minute compile. Full output in `pyplan/gates/t38-build-failure-reports-the-command-2026-09-12/buildkit-failed-build.txt`; the shape is:

```
------
 > [3/3] RUN sh -c "...":
0.213 mod_transmog/src/Transmog.cpp:212:9: error: no member named GetGUID
0.213 1 error generated.
------
Dockerfile:3
--------------------
   1 |     FROM busybox:1.36
   ...
   3 | >>> RUN sh -c "..."
   4 |
--------------------
ERROR: failed to build: failed to solve: process "/bin/sh -c ..." did not complete successfully: exit code: 1
```

The compiler's own words sit between the two `------` fences. Everything after the closing fence is boilerplate, and the final `ERROR: failed to solve:` line embeds the entire `RUN` command, which is why it alone blows past the 400-character cap and arrives truncated from the left — Lac's `...\"` opening is that cap biting.

So the last five lines are the Dockerfile context and the ERROR line. The error is eight lines further up, and it was in the buffer the whole time: `KEEP_OUTPUT_LINES = 200` retains it. **Only the selection is wrong**, which is why this is a fix in one function.

## Not the module bug

This ticket does not fix "modules don't work" — it makes that bug reportable. Lac's actual build failure, and the "None of modules detected" report in `#-yulon` the same evening, are separate and want their own ticket once a real error message reaches a user.

## Codex adversarial review (2026-09-12): REJECT, then fixed

Four findings taken. The first was a regression the first pass introduced.

**1. The step block REPLACED the tail, which hid an out-of-memory build.** A
linker the kernel kills leaves a cheerful last line and puts the only signal in
the `ERROR:` line:

```
------
 > [3/3] RUN cmake --build .:
[95%] Linking CXX executable worldserver
------
ERROR: failed to solve: process "..." did not complete successfully: exited with code: 137
```

The first pass reported `[95%] Linking CXX executable worldserver` and threw the
137 away — worse than what it replaced, and on the failure this project warns
about before every build (`compiler jobs vs memory`). Both halves are now
returned: the block, then the `ERROR:` line with its embedded command elided
from the middle, which is the part that overran the cap in the first place.

**2. Fences were paired by taking the last two, not structurally.** A stray rule
anywhere between the block and the `ERROR:` line moved the boundary, and an odd
count or a `------` inside a step's own output silently chose two unrelated
rules. The opening fence is now the last one carrying a step header, and the
closing one the first fence after it.

**3. Any line starting with `>` was accepted as BuildKit's header.**
`last_words()` is shared with imports, clones and extractors. The header must
now match `> [n/m] ...`.

**4. Truncation kept the tail of the selected block.** Right for BuildKit's
command-heavy epilogue, exactly wrong for a compiler diagnostic, which puts
`file:line:column: error:` at the FRONT. The block is now cut from the head; the
fallback still keeps the tail.

**Not taken.** The reviewer is right that the fence-and-header grammar is a
presentation format Docker does not document as stable, and that `docker
compose` and the classic builder are not proven equivalent. What makes that
acceptable is the fallback, and the fallback is now pinned by a test:
classic-builder output has no fences, falls through, and a user sees exactly
what they saw before T38. Parallel-step interleaving the reviewer labelled
speculation is untouched — with the `ERROR:` line back, a mixed block is still
strictly better than the command.

## Evidence

Replicated on `m910q` (Docker 29.7.2) with a busybox stand-in that fails in
three seconds; the tool's behaviour was the question, so the payload did not
need to be a 40-minute compile ([[gate-the-tool-not-the-payload]]). Captured
output in `buildkit-failed-build.txt` beside this ticket.

What Lac would now see, from that real capture:

```
0.213 mod_transmog/src/Transmog.cpp:212:9: error: no member named GetGUID /
0.213 1 error generated. / ERROR: failed to build: failed to solve: process
"/bin/sh -c sh -c \"e...erated.\\\" >&2; exit 1\"" did not complete
successfully: exit code: 1
```

Ten tests. Six mutations, `__pycache__` purged on both sides of each. **Three
survived their first fixture and each needed a better one** — accepting any `>`
(the old fixture's `ERROR 1064` has no colon, so the failure marker rejected it
before the guard was reached), pairing by the last two fences (no fixture had a
stray rule), and deleting the `ERROR:` gate entirely (twice: the first
replacement assertion held under the mutation too, because a parse that happens
to include the last line looks like a fallback unless you assert the raw fences
survive). 6/6 now.

Suite 4353 passed, 31 skipped; ruff, black and mypy clean.

## Still owed

- The gate on a box, and `--checks`.
- A reply to Lac — and the module bug underneath his build is still unfixed.
