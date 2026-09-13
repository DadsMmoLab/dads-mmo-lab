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

## Round 2 — the field refuted the fixture, 2026-09-13

The reporter ran `v0.8.67-fixtest`, which carries round 1, and sent a screenshot. The fix
worked — the cmake line is gone and he has a link:

```
FAILED: InstallerError: the build failed (exit 1). Its last words were: Docker Desktop
kept this build's log instead of printing it:
docker-desktop://dashboard/build/default/default/xbeswht6sps99pdrz6sck5st7
```

**And the exit code is missing** — the half this ticket called load-bearing, because
`docker build` returns 1 even when the step inside it was OOM-killed with 137.

`_exit_code(error_line or said[-1])` looked in two places and his stream has it in neither:
`error_line` is empty because `_buildkit_failure()` recognises only a line beginning `ERROR:`
and his output has none, and `said[-1]` is the `View build details:` URL. The clause sits on
an earlier line.

**Why round 1's test did not catch it.** `_DOCKER_DESKTOP_TAIL` carried an
`ERROR: failed to solve:` prefix **that the lead wrote**. The screenshot showed that line
left-truncated (`...MPILER_LAUNCHER=`), and the prefix was reconstructed from what the code
expected rather than from what was on screen. That is the exact failure this ticket's own
provenance note warns about, committed in the same commit as the warning:

> an invented one would have had the fences this one is missing

The fences were right. The `ERROR:` prefix was not, and one invented token was enough to make
the test agree with the bug.

**Fixed:** the whole tail is searched for the clause, last match wins. Verified against his
real output and against the 137 case:

```
exit code: 1 — Docker Desktop kept this build's log instead of printing it: …xbeswht6sps…
exit code: 137 — Docker Desktop kept this build's log instead of printing it: …abc
```

Fixture corrected to what he actually has. Gate `4370 passed`; M4 (restore the two-place
lookup) killed.

## Review round 1 — the branch was firing for callers it was never about

Adversarial Codex review, 2026-09-13. Verdict **needs-attention**.

**HIGH — `last_words()` is shared, and the Docker Desktop branch is the BUILD's.** Imports,
extractors, map generation and plain `docker run`s all reach this function, and container
output is not ours to predict. A line reading `View build details: …` can arrive in any of
those tails — left over from an earlier build in a compose stream, or printed by the image
itself — and the branch fired on nothing more than that string being present. It then
**discarded the real final lines** and told the user to open a link, which on a headless box or
under WSL resolves to nothing at all. Worse than the message it replaced.

Fixed by making it opt-in: `last_words(tail, *, from_build=False)`, and exactly one caller
passes `from_build=True` — `native.py:4565`, `self._check_run(run, "the build", …)`. Every
other caller keeps the behaviour it had.

**MEDIUM — the exit code and the URL were chosen independently.** Both were reverse-searched
over the whole 200-line tail, so a later unrelated `exit code:` clause could be attributed to
the linked build. `137` is the one that matters: it is an out-of-memory kill and sends recovery
in a different direction from an ordinary exit 1. Docker prints the URL *after* the failure, so
the code is now taken from at-or-before the link.

**Also taken from the review:** the plain tail is kept BESIDE the link rather than replaced by
it, because `docker-desktop://` opens nothing where Docker Desktop is not installed — and this
app runs headless and under WSL.

Three tests added for exactly these: a non-build caller with that line in its tail, a tail with
two exit codes either side of the link, and a tail where the plain lines must survive.

Gate after: `4373 passed, 8 skipped`. ruff/black clean, mypy clean on all three.
