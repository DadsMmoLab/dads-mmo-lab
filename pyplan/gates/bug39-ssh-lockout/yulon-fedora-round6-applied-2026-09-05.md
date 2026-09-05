# yulon-fedora: what round 6 actually applied, and what it put back

**Read on 2026-09-05 at 17:06 UTC, by round 10, read-only.** Every command in this file is a
`journalctl`, a `firewall-cmd` listing (`--list-*`, `--get-*`, `--state`) or `systemctl list-units`;
nothing was applied by round 10, on this box or any other. **Corrected after round 10's
review-of-review — see the addendum at the end**: several of this file's universals were refuted
by the journal it read. This file exists because the strings `b39-failsafe`, `add-port` and `1025-65535` occurred
nowhere under `pyplan/gates/bug39-ssh-lockout/` and nowhere in bug-checklist §39 before it
(`grep -rn "b39-failsafe\|1025-65535\|add-port" pyplan/` returned nothing at `4fb61ea9`), so the
only live `apply()` this folder had recorded was the two words "own clean run" (an earlier one,
in boot `-5`, was found afterwards — addendum).

## The command that read it

```
ssh yulon-fedora 'sudo -n journalctl _COMM=sudo --since today --no-pager -o short-iso --utc'
```

2733 lines today. Filtered to the WRITES
(`grep -E "add-port|remove-port|--reload|systemd-run"`), it is **39 lines**, all of them in boot
`-4` (`d79e921641e0464e9d0007f50123820e`, 2026-09-05 05:31:30 -> 06:08:03 UTC), between 06:00:22
and 06:07:39 UTC. That window closes **three minutes before round 6's own commit**
(`git log --date=iso-strict lane/bug39-r6`: `ee361035` at `2026-09-05T08:10:40+02:00` = 06:10:40
UTC) and six hours before round 7's (`ef022b3a`, `14:44:46+02:00` = 12:44:46 UTC), which is what
places it in round 6 and leaves bug-checklist §39's "nothing in rounds 7, 8, 9 or 10 ran `apply()`"
true. **Not measured:** whether any *earlier* boot on this box carries firewall writes — `--since
today` was the window read, and boots `-5` and older were not asked. What was missing before this
file was any record that the lane had applied anything on a real Fedora box at all.

## The 39 writes, in order

```
2026-09-05T06:00:22+00:00  /usr/sbin/systemd-run --on-active=420 --unit=b39-failsafe /usr/bin/systemctl stop firewalld
2026-09-05T06:00:39+00:00  /usr/sbin/firewall-cmd --permanent --zone=FedoraWorkstation --add-port=3724/tcp
2026-09-05T06:00:39+00:00  /usr/sbin/firewall-cmd --permanent --zone=docker --add-port=3724/tcp
2026-09-05T06:00:40+00:00  /usr/sbin/firewall-cmd --permanent --zone=FedoraWorkstation --add-port=8085/tcp
2026-09-05T06:00:40+00:00  /usr/sbin/firewall-cmd --permanent --zone=docker --add-port=8085/tcp
2026-09-05T06:00:40+00:00  /usr/sbin/firewall-cmd --permanent --zone=FedoraWorkstation --add-port=22/tcp
2026-09-05T06:00:41+00:00  /usr/sbin/firewall-cmd --permanent --zone=docker --add-port=22/tcp
2026-09-05T06:00:41+00:00  /usr/sbin/firewall-cmd --reload
2026-09-05T06:03:12+00:00  /usr/sbin/firewall-cmd --permanent --zone=FedoraWorkstation --remove-port=22/tcp
2026-09-05T06:03:13+00:00  /usr/sbin/firewall-cmd --permanent --zone=FedoraWorkstation --remove-port=3724/tcp
2026-09-05T06:03:13+00:00  /usr/sbin/firewall-cmd --permanent --zone=FedoraWorkstation --remove-port=8085/tcp
2026-09-05T06:03:13+00:00  /usr/sbin/firewall-cmd --permanent --zone=docker --remove-port=22/tcp
2026-09-05T06:03:14+00:00  /usr/sbin/firewall-cmd --permanent --zone=docker --remove-port=3724/tcp
2026-09-05T06:03:14+00:00  /usr/sbin/firewall-cmd --permanent --zone=docker --remove-port=8085/tcp
2026-09-05T06:03:15+00:00  /usr/sbin/firewall-cmd --reload
2026-09-05T06:03:25+00:00  /usr/sbin/firewall-cmd --permanent --zone=FedoraWorkstation --remove-port=1025-3723/tcp
2026-09-05T06:03:26+00:00  /usr/sbin/firewall-cmd --permanent --zone=FedoraWorkstation --remove-port=3725-8084/tcp
2026-09-05T06:03:26+00:00  /usr/sbin/firewall-cmd --permanent --zone=FedoraWorkstation --remove-port=8086-65535/tcp
2026-09-05T06:03:27+00:00  /usr/sbin/firewall-cmd --permanent --zone=FedoraWorkstation --add-port=1025-65535/tcp
2026-09-05T06:03:27+00:00  /usr/sbin/firewall-cmd --reload
2026-09-05T06:07:16+00:00  /usr/sbin/systemd-run --on-active=420 --unit=b39-failsafe2 /usr/bin/systemctl stop firewalld
2026-09-05T06:07:18+00:00  /usr/sbin/firewall-cmd --permanent --zone=FedoraWorkstation --add-port=3724/tcp
2026-09-05T06:07:19+00:00  /usr/sbin/firewall-cmd --permanent --zone=docker --add-port=3724/tcp
2026-09-05T06:07:19+00:00  /usr/sbin/firewall-cmd --permanent --zone=FedoraWorkstation --add-port=8085/tcp
2026-09-05T06:07:19+00:00  /usr/sbin/firewall-cmd --permanent --zone=docker --add-port=8085/tcp
2026-09-05T06:07:20+00:00  /usr/sbin/firewall-cmd --permanent --zone=FedoraWorkstation --add-port=22/tcp
2026-09-05T06:07:20+00:00  /usr/sbin/firewall-cmd --permanent --zone=docker --add-port=22/tcp
2026-09-05T06:07:21+00:00  /usr/sbin/firewall-cmd --reload
2026-09-05T06:07:35+00:00  /usr/sbin/firewall-cmd --permanent --zone=FedoraWorkstation --remove-port=22/tcp
2026-09-05T06:07:35+00:00  /usr/sbin/firewall-cmd --permanent --zone=FedoraWorkstation --remove-port=3724/tcp
2026-09-05T06:07:35+00:00  /usr/sbin/firewall-cmd --permanent --zone=FedoraWorkstation --remove-port=8085/tcp
2026-09-05T06:07:36+00:00  /usr/sbin/firewall-cmd --permanent --zone=docker --remove-port=22/tcp
2026-09-05T06:07:36+00:00  /usr/sbin/firewall-cmd --permanent --zone=docker --remove-port=3724/tcp
2026-09-05T06:07:37+00:00  /usr/sbin/firewall-cmd --permanent --zone=docker --remove-port=8085/tcp
2026-09-05T06:07:37+00:00  /usr/sbin/firewall-cmd --permanent --zone=FedoraWorkstation --remove-port=1025-3723/tcp
2026-09-05T06:07:38+00:00  /usr/sbin/firewall-cmd --permanent --zone=FedoraWorkstation --remove-port=3725-8084/tcp
2026-09-05T06:07:38+00:00  /usr/sbin/firewall-cmd --permanent --zone=FedoraWorkstation --remove-port=8086-65535/tcp
2026-09-05T06:07:39+00:00  /usr/sbin/firewall-cmd --permanent --zone=FedoraWorkstation --add-port=1025-65535/tcp
2026-09-05T06:07:39+00:00  /usr/sbin/firewall-cmd --reload
```

Everything else `sudo` logged today on this box that touched the firewall is a read: 128 `--get-active-zones`, 125
`--permanent --list-all-zones`, 70 `--state`, 6 `firewall-offline-cmd --list-all-zones`, 6
`--get-default-zone`, plus `--list-ports`, `--get-zone-of-interface` and `--version`
(`... | sed -E "s/.*COMMAND=//" | sort | uniq -c | sort -rn`). The same journal holds nineteen
non-firewall writes from the same run — accounts, a sudoers drop-in, `chmod`s — listed in the
addendum; a first version of this sentence read only the top of that `uniq -c` list.

## What that is, in words

* **Two failsafes, both cancelled rather than fired.** `systemd-run --on-active=420 --unit=b39-failsafe`
  and `--unit=b39-failsafe2` each armed a 7-minute `systemctl stop firewalld` before the run touched
  the firewall — the remote-lockout insurance this whole section is about. `journalctl -u
  "b39-failsafe*"` shows `Started b39-failsafe.timer` 06:00:22 and `Deactivated successfully` /
  `Stopped` 06:03:12, and the same pair for `b39-failsafe2` at 06:07:16 and 06:07:34 — both
  deactivated well inside their 420 s, and `journalctl -u firewalld` records no `Stopping` between
  05:31:38 and 06:08:03, so neither failsafe ever fired. (The `Stopping firewalld.service` at
  06:08:03 is that boot's own shutdown.) Cancelled by `pk` through `sudo`: the same `_COMM=sudo`
  journal carries `systemctl stop b39-failsafe.timer` at 06:03:12 and `systemctl stop
  b39-failsafe2.timer` at 06:07:34, the seconds the unit log shows them deactivated (a first
  version of this bullet called the caller unmeasured; the source it read had it).
* **Two apply/undo cycles, alike in shape and not identical**, 06:00-06:03 (20 commands, `--reload`
  at 06:00:41, 06:03:15 and 06:03:27) and 06:07:16-06:07:39 (19 commands, `--reload` at 06:07:21
  and 06:07:39): `--add-port=3724/tcp`, `8085/tcp` and `22/tcp` to BOTH `FedoraWorkstation` and
  `docker`, `--reload`, then the matching `--remove-port`s and another `--reload`.
* **Three range fragments removed that no `sudo` command in any boot on this box ever added**, then
  one range added. The undo removes `1025-3723`, `3725-8084` and `8086-65535` — added by nothing
  in boots `-7` through `0` (a first version of this bullet said a wide range had been "broken up";
  no journal line supports that) — and then adds `1025-65535/tcp`, which is the three fragments
  plus `3724` and `8085`; `22` lies below 1025 and is not part of it (a first version said it
  was). `firewall-cmd --permanent --zone=FedoraWorkstation --list-ports` today answers
  `1025-65535/tcp 1025-65535/udp`, so the end state matches the range that was re-added. **Not
  measured:** what `FedoraWorkstation` holds on a stock Fedora 44 install — that this range is the
  distribution default is not something this journal or this box can be asked.

## The box as it stands now

```
sudo -n firewall-cmd --permanent --zone=FedoraWorkstation --list-ports  ->  1025-65535/tcp 1025-65535/udp
sudo -n firewall-cmd --permanent --zone=docker --list-ports             ->  (empty)
sudo -n firewall-cmd --zone=FedoraWorkstation --list-ports              ->  1025-65535/tcp 1025-65535/udp
sudo -n firewall-cmd --get-default-zone                                 ->  FedoraWorkstation
sudo -n firewall-cmd --state                                            ->  running
systemctl list-units --all 'b39-failsafe*'                              ->  0 loaded units listed
```

Runtime and permanent agree, the `docker` zone carries no ports, and neither failsafe unit is left
behind. **Not measured:** whether the two cycles at 06:00 and 06:07 were two separate presses or one
press retried — the journal records the commands, not the caller, and no round-6 artefact in this
folder says which.

## Addendum — what round 10's review-of-review found in the same journal (2026-09-05, 17:42-19:03 UTC)

Every item below is a read of yulon-fedora's own journal (`sudo -n journalctl _COMM=sudo -b <n>
--no-pager -o short-iso --utc`, read-only), taken by the round-10 review-of-review and re-checked by
the session that merged the lane. They correct the universals this file first asserted.

* **An earlier live apply, in boot `-5`** (boot `cdc9bbe3…`, 2026-09-04 23:25:25 → 23:35:27 UTC):
  24 firewall writes at 23:32:34-23:35:16 UTC — `--add-port=3724/8085/22` to `FedoraWorkstation` and
  `docker`, `--reload`, a second add of 3724/8085 to both zones at 23:33:48-50 with no reload, a
  third `--add-port=3724/tcp` to `FedoraWorkstation` at 23:34:05, the six `--remove-port`s at
  23:35:00-02, `--reload`, the same three fragment removes and `--add-port=1025-65535/tcp` at
  23:35:14-16, `--reload` — and **no `systemd-run` failsafe anywhere in that boot**
  (`journalctl -b -5 -u 'b39-failsafe*'` → No entries). So the lane applied to a real Fedora box
  twice: on the night §39 was opened, without the insurance this file praises, and unrecorded until
  now. Boots `-6`/`-7` (2026-09-02) and `-3`…`0` carry no firewall writes.
* **Nineteen non-firewall writes in boot `-4`, from the same run:** 06:01:37 `useradd -m b39none`,
  `useradd -m b39lim`, `tee /etc/sudoers.d/b39lim`; 06:01:38 `chmod 440 /etc/sudoers.d/b39lim`,
  `visudo -c -f /etc/sudoers.d/b39lim`, `chmod -R a+rX /home/pk/lab /home/pk/v5`, `chmod a+x
  /home/pk`; 06:01:44/53 `env HOME=/home/b39none|b39lim /home/pk/v5/bin/python /home/pk/lab/attacks.py
  noauth|limited` as those users; 06:03:12 `systemctl stop b39-failsafe.timer` + `reset-failed`;
  06:03:15 `rm -f /etc/sudoers.d/b39lim`; 06:03:16 `userdel -r b39none`, `userdel -r b39lim`;
  06:07:34 `systemctl stop b39-failsafe2.timer`, `reset-failed b39-failsafe*`. The accounts and the
  drop-in were removed by the run itself (`id b39lim` / `b39none`: no such user, 19:02 UTC).
* **One change nobody reverted:** `chmod a+x /home/pk` left the home directory at `711`
  (`HOME_MODE 0700` in `/etc/login.defs`). Put back to `700` on 2026-09-05 at 19:02:50 UTC by the
  merging session (`chmod 700 /home/pk`; `stat -c %a` → 700). `/home/pk/v5` stays `755` from the
  `chmod -R a+rX`; `/home/pk/lab` no longer exists.
* **39, not 40:** the round-9 note that first named this run counted 40 writes; two filters over the
  3324-line journal (`add-port|remove-port|--reload|systemd-run`, with and without `stop firewalld`)
  both give 39. The 40 was not reproduced and the difference is not explained by any file.

