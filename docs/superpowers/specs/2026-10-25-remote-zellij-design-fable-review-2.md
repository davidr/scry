# Review 2: Remote zellij tabs in the scry index

**Reviewing:** `2026-10-25-remote-zellij-design.md` (revised draft, after the Fable and
Astra reviews)
**Reviewer:** Claude (Fable 5.1), time-boxed (~10 min), big issues only, for the stated
usage: one person, ~10 terminals, two or three remote hosts.
Claims about existing code were checked against `src/scry/scry.py`,
`src/scry/backends/tmux.py`, `src/scry/backends/zellij.py`,
`src/scry/backends/pty_client.py`. zellij behaviour was spot-checked on zellij 0.45.1
with a throwaway session (`scry-test-fablerev2`, deleted afterwards).

## Verdict

The revised design is sound for this usage. The earlier reviews' substantive points
(session identity source, snapshot-bound selection, name-based verification, BatchMode
contract, path/quoting policy, literal rendering, rename cut) are folded in correctly.
I found **one issue that will bite in daily use** (the Ctrl-C escape hatch kills the
local scry), one tuning concern, and some builder notes. Nothing else big is left.

## Code and behaviour claims verified

- **No nesting under the tmux backend.** `TmuxBackend.attach_window` runs
  `tmux attach-session -t <session>:<window>` (`tmux.py` line 209), so the local scry
  runs *outside* tmux in the terminal emulator. A foreground `ssh -t` from scry therefore
  puts the remote zellij directly on the emulator, as the design claims. Same for the
  zellij backend (scry owns the pty, it is not inside a zellij client).
- **Session identity source works.** On 0.45.1 the server process's cmdline is exactly
  `<zellij-bin> --server /run/user/<uid>/zellij/contract_version_1/<session-name>`
  (argv[1] is `--server`, basename of argv[2] is the session name), its parent is PID 1
  (daemonized), and `/proc/<pid>/stat` field 22 is a plain integer. This is a usable,
  restart-sensitive identity.
- `action` commands on a missing/EXITED session fail rather than resurrect; only
  `zellij attach <name>` resurrects. The "check before `attach`" rule is right and
  `_attach`'s existing `_list_clients`/`_list_tabs` snapshot (`zellij.py` lines 399-405)
  already fails safely.
- `parse_args_and_configure()` and `backend = get_backend(config)` run at import time
  (`scry.py` lines 189-196); `process_command` matches `n ` / `r ` with a trailing space,
  so `<key><n>` selections such as `n0` cannot be mistaken for a new-window command. The
  design's "route around import-time setup" note is accurate and necessary.
- `proxy()` saves and restores termios in a `finally` (`pty_client.py` lines 250-274),
  so the remote side of an attach restores its pty on every exit path; the local restore
  the design adds is the missing half for a dropped connection.

## Findings, by severity

### 1. High — The documented Ctrl-C escape hatch will kill the local scry

The design (Attach UX, line 361-363) tells the user to press Ctrl-C while ssh is still
connecting. At that point the local tty is cooked, and `ssh -t` is run in the foreground
inheriting the tty and scry's process group. Ctrl-C therefore delivers SIGINT to **both**
ssh and the local scry. scry has no SIGINT/`KeyboardInterrupt` handling anywhere
(`grep` of `src/scry/` finds none), so the index process dies with a traceback and, if
scry was the terminal emulator's command, the window closes.

**Why it matters here:** waking a stopped Coder workspace is the one slow path the user
will hit regularly, and this is the only recommended way out of it. Losing the scry
instance (and its history) every time is exactly the "works badly in daily use" case.
The same applies to `z`: with `start_new_session=True` the ssh workers are *not* in
scry's group, so Ctrl-C during a blocking `z` hits only scry and kills it.

**Suggest:** while the foreground ssh child runs, set SIGINT to `SIG_IGN` in the parent
and restore it in `finally` (the child inherits the default disposition only if scry
restores it before `exec`, so set it via `preexec_fn`-free means: `subprocess.run(...,
restore_signals=True)` already resets SIG_IGN to default in the child — verify). Ctrl-C
then reaches ssh alone, ssh exits non-zero, and scry maps that to "connection cancelled".
For `z`, catch `KeyboardInterrupt` around the wait, `killpg` the workers, keep the old
caches, and redraw with "refresh cancelled". Add both to the unit tests.

### 2. Medium — `remote_timeout` 5 s is tight for a cold Coder connection

A cold `coder ssh --stdio` handshake is commonly 1-3 s, then the remote login shell,
the `uv` tool shim and Python startup (~0.3-0.5 s), then two zellij CLI calls. Without a
live ControlMaster this will sometimes exceed 5 s, so `z` will intermittently mark a
healthy host "unavailable" and the user will learn to press `z` twice. Related: on
timeout the design `killpg`s the worker's group. If the first connection is the one
spawning the `ControlPersist` master, check whether the master lands in the same
process group (it forks but I am not certain it calls `setsid`); if it does, a timed-out
`z` also kills the master that would have made the *next* `z` fast.

**Suggest:** default `remote_timeout` to 10 s (the user confirmed a few seconds of
blocking is fine, and the slow case is rare), and have the builder confirm `killpg`
vs. the ControlMaster master on a real host during setup-checklist step 7.

### 3. Low — Builder notes on session-identity discovery (so it doesn't fail closed for the wrong reason)

Confirmed working, but two easy traps:

- Do not find the server with a substring match (`pgrep -f <name>` style). On the
  remote, the user's own `zellij attach main` clients and any shell whose command line
  mentions the session name also match, which would trip the "ambiguous → fail closed"
  rule. Match `argv[1] == "--server"` and `basename(argv[2]) == session_group`
  exactly; `argv[0]` varies with the install path, so don't match on it.
- `/proc/<pid>/stat` field 22 must be parsed after the last `)` (the `comm` field can
  contain spaces and parentheses). A naive `split()[21]` works for `zellij` today but
  is a one-line fix to get right.

## Checked and not a problem in this usage

- Snapshot-bound selection, name-sorted indices, history keyed by
  (host, identity, tab ID), and the history-cleanup rule are consistent with how
  `do_table_loop` already flows `windows` into `process_command`.
- Framing with sentinels handles the bash-sources-`~/.bashrc`-for-ssh-commands case,
  which is the realistic source of noise.
- Exit-code mapping: ssh returns the remote command's status, 255 for its own failures
  (including, as far as I know, a remote command killed by a signal), 127 from the login
  shell when `scry` isn't found, and the old scry's argparse exits 2 on an unknown
  positional. The three "outside" codes are the right ones to special-case.
- `$XDG_RUNTIME_DIR` may be unset in a Coder container's non-interactive ssh session;
  the `/tmp` fallback for the attach lock covers it.

## Summary

Fix finding 1 before building (it is small). Consider finding 2 as a default tweak.
Finding 3 is a note for the plan. Otherwise the design is ready for implementation
planning.
