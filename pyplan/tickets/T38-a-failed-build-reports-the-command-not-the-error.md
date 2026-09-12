# T38 — a failed build reports the command that failed, never the error that failed it

**Status:** OPEN — replicated; fix and test next
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

## Still owed

- The failing test, from the captured tail.
- The fix in `last_words()`.
- A review.
- A reply to Lac.
