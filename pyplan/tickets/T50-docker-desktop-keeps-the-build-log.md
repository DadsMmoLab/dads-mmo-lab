# T50 — on Docker Desktop the compiler's error is not in the stream at all, and T38 assumed it was

**Status:** FILED — from the reporter's own screenshots, 2026-09-13 17:08.
**Filed:** by the lead. T38 shipped in #151 and did not fix this user's message.
**Branch:** `fix/build-log-kept-by-docker-desktop`, from `upstream/Yulon`.

## What the user got, on the build that carries T38's fix

He is provably on `0.8.66-fixtest` — module removal worked for him, and that only exists
there ("removing is working confirmed"). The build then failed and said:

```
FAILED: InstallerError: the build failed (exit 1). Its last words were:
  ...MPILER_LAUNCHER="ccache" -DCMAKE_C_COMPILER_LAUNCHER="ccache" …
```

The **leading `...`** is the diagnosis. `last_words()` only produces that prefix on its final
line:

```python
return text if len(text) <= _LAST_WORDS_CHARS else "…" + text[-_LAST_WORDS_CHARS:]
```

which is reached only when `_buildkit_failure()` returned an empty block. So the extractor
found nothing, and the user was shown the middle of a cmake invocation — the exact complaint
T38 was filed for.

## Why it found nothing

His tail, from the screenshot:

```
… did not complete successfully: exit code: 1
[11:07:30]
[11:07:30]
[11:07:30] View build details: docker-desktop://dashboard/build/default/default/c6g4h689yvm5emfs0xe29iae7
[11:07:30]
```

There is no `------` fence, no `> [n/m] RUN …` step header, and no step output. **The
compiler's words are not in the stream.** Docker Desktop kept them for its own Build view
and printed a URL instead.

This is not a buffer problem and not a `--progress` problem — `docker.py:3496` already passes
`--progress plain`, and the capture keeps 200 lines.

## The premise that was wrong

T38's own docstring states it:

> The compiler's words are between the fences, and were never missing from the buffer:
> `KEEP_OUTPUT_LINES` keeps 200.

True of Docker CE on Linux, which is where it was measured
(`pyplan/gates/t38-build-failure-reports-the-command-2026-09-12/`, m910q, Docker 29.7.2).
False on Docker Desktop for Windows. A guarantee stated from one platform's measurement,
refuted by the first user on another — the shape `a-guarantee-is-an-invitation` is about.

## The fix

When the step block is empty **and** the tail carries a build-details URL, say that, and give
the URL. Never fall back to quoting the middle of a shell command: it is the one thing already
known to be useless to a reader.

What the sentence must carry, in order:

1. **The exit code**, elided from the `ERROR:`/`did not complete successfully` line. It is
   load-bearing on its own: `137` is an out-of-memory kill and nothing else says so.
2. **Where the log actually is**, verbatim, so it can be pasted into a browser.

## Definition of done

1. `last_words()` prefers a build-details URL over the left-truncated command echo, with a
   fixture built from **this user's actual output**, not an invented one.
2. The Linux shape is unchanged — asserted, because that is what T38 fixed and what the
   existing gate covers.
3. A tail with neither fences nor a URL still degrades to what it does today.

## Evidence the ticket owes

The gate's last line, a named mutation per test, and the fixture recorded beside the ticket
with its provenance (a screenshot in a DM, transcribed).

## Done — evidence

**Status:** FIXED 2026-09-13.

| | |
|---|---|
| before | `the build failed (exit 1). Its last words were: …MPILER_LAUNCHER="ccache" … -DBoost_USE_STATIC_LIBS="ON" && cmake --build …` |
| after | `exit code: 1 — Docker Desktop kept this build's log instead of printing it: docker-desktop://dashboard/build/default/default/c6g4h689yvm5emfs0xe29iae7` |

The Linux shape is asserted unchanged by its own test, because the new branch is reached from
the same `if not block` fallback T38 left behind and a condition written one word too wide
would have taken the fenced path with it.

**Gate:** `4369 passed, 8 skipped, 23 deselected`. ruff, black, mypy clean on `linux`,
`win32`, `darwin`.

**Mutations**, 3 run, 3 killed (`pyplan/gates/t50-docker-desktop-build-log-2026-09-13/mutations/`):

| | | |
|---|---|---|
| M1 | the URL is never looked for | falls back to the command echo |
| M2 | the exit code is dropped | `137` would stop being sayable |
| M3 | the Linux branch swallows every case | the fenced path stops being reachable |

The fixture's provenance is beside the gate: `the-fixture-and-where-it-came-from.md`.

## Still owed

A press against a real Docker Desktop failure. The fixture is a faithful transcription, but it
is still a transcription — only a real failed build on Windows proves the URL is matched as it
is actually printed.
