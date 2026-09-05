# yulon-fedora: what round 6 actually applied, and what it put back

**Read on 2026-09-05 at 17:06 UTC, by round 10, read-only.** Every command in this file is a
`journalctl` or a `firewall-cmd --list-*`; nothing was applied by round 10, on this box or any
other. This file exists because the strings `b39-failsafe`, `add-port` and `1025-65535` occurred
nowhere under `pyplan/gates/bug39-ssh-lockout/` and nowhere in bug-checklist §39 before it
(`grep -rn "b39-failsafe\|1025-65535\|add-port" pyplan/` returned nothing at `4fb61ea9`), so the
only live `apply()` this lane has ever run on a real Fedora box was recorded as the two words "own
clean run".

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

Everything else `sudo` logged today on this box is a read: 128 `--get-active-zones`, 125
`--permanent --list-all-zones`, 70 `--state`, 6 `firewall-offline-cmd --list-all-zones`, 6
`--get-default-zone`, plus `--list-ports`, `--get-zone-of-interface` and `--version`
(`... | sed -E "s/.*COMMAND=//" | sort | uniq -c | sort -rn`).

## What that is, in words

* **Two failsafes, both cancelled rather than fired.** `systemd-run --on-active=420 --unit=b39-failsafe`
  and `--unit=b39-failsafe2` each armed a 7-minute `systemctl stop firewalld` before the run touched
  the firewall — the remote-lockout insurance this whole section is about. `journalctl -u
  "b39-failsafe*"` shows `Started b39-failsafe.timer` 06:00:22 and `Deactivated successfully` /
  `Stopped` 06:03:12, and the same pair for `b39-failsafe2` at 06:07:16 and 06:07:34 — both
  deactivated well inside their 420 s, and `journalctl -u firewalld` records no `Stopping` between
  05:31:38 and 06:08:03, so neither failsafe ever fired. (The `Stopping firewalld.service` at
  06:08:03 is that boot's own shutdown.) **Not measured:** what cancelled them; the journal names
  the timers, not the caller.
* **Two identical apply/undo cycles**, 06:00-06:03 and 06:07:16-06:07:39: `--add-port=3724/tcp`,
  `8085/tcp` and `22/tcp` to BOTH `FedoraWorkstation` and `docker`, `--reload`, then the matching
  `--remove-port`s and another `--reload`.
* **A wide range was broken up and put back.** The undo removes three ranges nobody in this journal
  ever added — `1025-3723`, `3725-8084`, `8086-65535` — and then adds `1025-65535/tcp`, which is
  exactly the three fragments plus the three single ports `22`, `3724` and `8085` that the cycle had
  added. `firewall-cmd --permanent --zone=FedoraWorkstation --list-ports` today answers
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
