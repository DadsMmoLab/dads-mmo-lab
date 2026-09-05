# Correction to `ss-format-live.txt` — 2026-09-04, later the same day

`ss-format-live.txt` was captured read-only on `dml-arch` (WSL2, Arch) and settles four things.
Points 1, 2 and 3 — the long flags are accepted, the column layout is
`State Recv-Q Send-Q Local:Port Peer:Port` so `fields[3]`'s last colon holds the port, and the owner
column is literally `users:(("name",pid=N,fd=N))` — were re-checked against `m910q` (Ubuntu, real
sshd) and hold there too. **Its point 4 and its closing "STILL UNVERIFIED" paragraph do not, and
the README repeats both.** The capture itself is left untouched; this file is the correction.

## 1. "as a normal user there is NO `users:((` column at all" — refuted

That was true of `dml-arch` and is a property of `dml-arch`, not of an unprivileged `ss`. What an
unprivileged `ss` hides is the owner of a socket the user **does not own**. `dml-arch` happened to
have zero user-owned listeners, so every owner column vanished at once and the generalisation
looked safe.

Measured on `m910q` (Ubuntu 24.04, `pk` = uid 1000, sshd running), read-only, nothing changed:

```
$ ss --no-header --listening --tcp --numeric --processes      # as pk
LISTEN 0      128          0.0.0.0:22    0.0.0.0:*
LISTEN 0      128        127.0.0.1:5939  0.0.0.0:*
LISTEN 0      4096         0.0.0.0:3724  0.0.0.0:*
LISTEN 0      4096    127.0.0.53%lo:53    0.0.0.0:*
LISTEN 0      4096     100.78.24.50:45057 0.0.0.0:*
LISTEN 0      4096        127.0.0.1:3306  0.0.0.0:*
LISTEN 0      4096         0.0.0.0:8085   0.0.0.0:*
LISTEN 0      128        127.0.0.1:631    0.0.0.0:*
LISTEN 0      128             [::]:22       [::]:*
LISTEN 0      10                 *:3389        *:* users:(("gnome-remote-de",pid=1067,fd=8))
LISTEN 0      4096            [::]:3724     [::]:*
LISTEN 0      128            [::1]:631      [::]:*
LISTEN 0      4096   [fd7a:...]:49179      [::]:*
LISTEN 0      4096            [::]:8085     [::]:*

$ ps -o user=,pid=,comm= -p 1067
pk          1067 gnome-remote-de
```

One line carries an owner column, and it is the desktop's own remote-desktop listener, owned by the
user running the probe. sshd's `:22` lines carry none — they are root's. As root the same command
attributes every line, `0.0.0.0:22` included (`users:(("sshd",pid=626,fd=3))`).

Consequence, re-derived on the box by piping that exact output through the partition in
`_sshd_listening_ports()`:

```
ports = []
listeners_readable = True
```

So `listeners_readable` did not mean "this probe could see who owns things". It meant "some socket
on this box is owned by me", which on any desktop is almost always true.

## 2. "a wrong owner token lands on the REFUSE branch, not on a silent enable" — false

Both `ss-format-live.txt` and `README.txt` close with that sentence. The REFUSE branch in
`_guard_the_way_back_in()` is reached only when `listeners_readable` is False **or**
`SSH_CONNECTION` is set (`networking.py:256-276` at `4c959d70` — line numbers pinned to the
commit, because the file was being repaired the same night). A probe that read the table, found
no `"sshd` token and ran without `SSH_CONNECTION` — a GUI session, a `sudo`-stripped environment, a systemd
unit — falls into `if not asked.connected and asked.listeners_readable`, which returns the three
commands untouched with empty `refusals` and empty `warnings`. A wrong owner token therefore lands
on the **silent enable**, which is the original lockout.

Two ways the token goes wrong were found, and neither needs a rename upstream:

* the socket is root-owned and the probe is not root — section 1;
* the listener belongs to a socket-activated sshd, where the owner reads
  `users:(("systemd",pid=1,fd=150))`. `'"sshd'` does not match that, so even a **root** probe reads
  the box as having no sshd. The shape is in this repo:
  `pyplan/gates/7.1-ubuntu-2026-09-04-clean/gate71-realm-and-account.log`, `sudo ss -lntp` on
  `yulon-ubuntu`, prints `users:(("sshd",pid=17501,fd=3),("systemd",pid=1,fd=150))` for port 22.

## 3. What the capture still legitimately settles

Its "STILL UNVERIFIED" line asked whether a live sshd listener prints as `sshd`. It does:
`m910q`'s root `ss` shows `users:(("sshd",pid=626,fd=3))` on `0.0.0.0:22` and `[::]:22`, and
`yulon-ubuntu`'s shows `("sshd",pid=17501,...)` alongside the systemd socket unit. The token is
right; what the fix reads it with is not.

Full write-up in `pyplan/bug-checklist.md` §39.
