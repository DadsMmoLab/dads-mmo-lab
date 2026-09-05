LANE A -- bug-checklist §39, the LAN step that locks you out of a remote Linux box
2026-09-04. Code only: pylauncher/yulon/networking.py and
pylauncher/tests/test_networking.py. Nothing was run against a server; every
test ran on yulon-win11-gate in the prepared venv. Not committed.

WHAT WAS WRONG
  plan()  ->  [['ufw','allow','3724/tcp'], ['ufw','allow','8085/tcp'],
               ['ufw','--force','enable']]
  ufw enable brings up default-deny-incoming with only those two ports allowed,
  so SSH dies on every box reached over the network, and report.skipped /
  report.manual_steps were both empty. Evidence:
  pyplan/gates/7.1-ubuntu-2026-09-04/ufw-lockout.txt

FILES HERE
  red-1-no-seam.txt        first RED: the tests cannot even be collected,
                           `module 'yulon.networking' has no attribute 'SshRoute'`.
  red-2-behaviour.txt      second RED: the seam exists but does nothing, so each
                           test fails on its own assertion -- 14 failed, 51
                           passed, every failure naming ('ufw','--force','enable')
                           still in the plan or ['sudo','-n','ufw','--force',
                           'enable'] still in the run log.
  green-and-checks.txt     65 passed in test_networking.py; black, ruff and mypy
                           clean; whole unit suite 2311 passed, 24 skipped.
  plan-and-report-after.txt the same fields the 7.1 gate read off account-lan.log,
                           for all five cases the fix distinguishes.
  mutations.txt            7 mutations, each re-arming one part of §39; 7/7
                           killed, with the test that caught each one named.
                           Run in a private copy at C:/gate/bug39-mut (since
                           deleted) so the shared checkout was never mutated,
                           and __pycache__ purged on both sides of every edit.
  ss-format-live.txt       the one assumption behind the port probe, checked
                           against a live `ss` (WSL2/Arch, read-only): column
                           layout, the users:(("name",pid=..)) shape, and the
                           measured fact that an UNPRIVILEGED ss prints no owner
                           column at all -- which is why "found no sshd" is not
                           allowed to mean "there is no sshd".

THE THREE DECISIONS, AND WHERE THEY ARE ARGUED IN THE CODE
  1. The SSH port comes from the RUNNING system: `SSH_CONNECTION` field four
     (the port sshd accepted this session on) unioned with `ss --listening
     --tcp --processes`. Never sshd_config -- see _SS_ARGV's docstring.
  2. The enable is OPT-IN (`plan(enable_firewall=True)`), and off by default:
     `ufw allow` lands whether ufw is on or off, so the enable cannot advance
     the user's request and can only destroy access. Argued in
     _guard_the_way_back_in(). The other path stays reachable rather than
     impossible -- it is one keyword argument, and it is what the enable tests
     drive.
  3. "Am I remote" is answerable only in one direction. SSH_CONNECTION set
     proves remote; unset proves nothing (sudo resets the environment, tmux
     predates the login, a systemd unit never had it). So the guard never uses
     "not remote" to allow anything on its own -- it needs a readable listener
     table saying nothing listens for SSH.

THE HARD REQUIREMENT
  No path leaves a box whose only route in is SSH without a rule admitting SSH.
  If the port cannot be established the enable is dropped and the refusal is on
  NetworkPlan.refusals, in NetworkPlan.warnings (what the controller view
  renders today) and in NetworkReport.skipped and .refusals -- the two lists the
  7.1 gate read, which were empty while the machine was being locked.
  apply() re-checks: if `ufw allow <ssh-port>/tcp` did not actually land in
  `done`, the enable behind it is refused too, because a plan can only declare
  that SSH survives.

ONE THING STILL UNVERIFIED
  That a live sshd listener prints as "sshd" (or "sshd-session" on OpenSSH 9.8+)
  in ss's owner column. The WSL distro used for the format check has no sshd.
  If that token is ever wrong the probe finds no port, which lands on the
  REFUSE branch -- never on a silent enable. Worth one read-only
  `ss --no-header --listening --tcp --numeric --processes` as root on any box
  that runs sshd, next time a lane owns one.
