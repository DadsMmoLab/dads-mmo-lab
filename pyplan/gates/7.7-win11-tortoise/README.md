# 7.7 — WoW Tortoise on native Windows, `yulon-win11-gate`, 2026-09-04/05

The run that earned `wow-tortoise` its `"windows"` platform and moved its ready
budget from 3600 s to 10800 s. Everything here was copied off the box on
2026-09-05 at 10:04 CEST, while the three containers were still up.

## What happened, in the box's own stamps (box shows PST; CEST = +9 h)

| stamp (box-local)        | event                                                      | source |
|--------------------------|------------------------------------------------------------|--------|
| 09/04 13:39:30 → 13:57:58 | client tar (9,981,655,040 B) downloaded at 9.0 MB/s         | `tortoise-dl.log` |
| 09/04 13:58:58 → 14:06:07 | md5 `5bca6fa4…` matched; 197 entries unpacked               | `tortoise-unpack.log` |
| 09/04 14:11:35, 14:16:58  | two install starts that ended errorlevel 1 within seconds   | `tortoise77-wrapper.log` |
| 09/04 14:18:59            | the install that ran (`==== install start`)                 | `tortoise77-wrapper.log` |
| 09/04 23:41:37            | step 11 `up`: `compose up -d --no-deps …`                   | `tortoise77.log` |
| 09/05 06:41:58Z           | `tortoise-mangosd` `StartedAt`                              | `tortoise-final/live-recheck.txt` |
| 09/05 07:41:17Z           | `World server is up and running! Loading time: 59 minutes 18 seconds` | `live-recheck.txt` (from `docker logs -t`) |
| 09/05 00:43:19            | `install of wow-tortoise finished`, errorlevel **0**         | `tortoise77.log`, `tortoise77.exitcode` |

Ready stage wall: 23:41:37 → 00:43:19 = **3702 s**. The repo's budget was 3600 s;
the copy of the catalog on the box (`C:\gate\tortoise-src`) carried 10800 s, which
is why this run was reported as what it was. Container `RestartCount=0` for all
three; ports `3724` (realmd) and `8090` (mangosd) published on `0.0.0.0`, `3306`
on `127.0.0.1`; the realm advertises `172.30.52.119`.

The two failed starts at 14:11 and 14:16 are the preflight refusals recorded in
`checklist.md` 7.7 (10 GB free against 40 needed), answered by the hot-added
`D:` disk. Server folder is therefore `D:\gate\tortoise-server`, client on `C:`.

## A confound, kept on purpose

`tortoise-dl.log` and `tortoise-unpack.log` each carry a SECOND entry. The
scheduled tasks `dml-tortoise-dl` / `dml-tortoise-unpack` fired once more at
23:59 / 23:58 box-local (both `One Time Only`, they will not fire again):
the unpack found no complete archive and stopped with `MD5 MISMATCH`
(`tortoise-unpack.exitcode` = 2 is THAT attempt, not the 14:06 one the install
used), and the download re-fetched all 9.98 GB at 6.6 MB/s from 23:59 to 00:23
— inside the world server's 59-minute load. The boot may be quicker on a quiet
box; the budget in the repo covers the boot that was measured.

## Files

- `tortoise77.log` — the installer's transcript, 29,6xx lines, UTF-8, CRLF. Twelve
  stage markers (`Step N of 12`); the `build` stage is lines 47–2943, `extract`
  2944–18049, `mmaps` 18050–29358.
- `tortoise77.exitcode` — `0`. `tortoise77-wrapper.log` — the three start/end pairs.
- `tortoise-dl.log` / `.exitcode`, `tortoise-unpack.log` / `.exitcode` — see above.
- `tortoise-final/docker-ps.txt` — `docker ps -a` at 00:45 box-local (BOM stripped).
- `tortoise-final/mangosd-tail.txt`, `realmd-tail.txt` — the last 399 / 100 lines
  of each container's log at 00:45. **Converted from UTF-16LE to UTF-8** here so
  `grep` works; the banner had already scrolled out of the mangosd tail (bot
  SQL heartbeat), which is why `live-recheck.txt` re-derives it from the
  container.
- `tortoise-final/live-recheck.txt` — captured 01:03 box-local: `docker inspect`
  `StartedAt`/`RestartCount` for all three, `docker ps` with ports, the
  timestamped banner from `docker logs -t`. Its last line is a realmlist query
  that failed on credentials (`$MARIADB_PASSWORD` is not set in that container);
  the realm address comes from the transcript's own final lines instead.
