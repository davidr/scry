# Review: Remote zellij tabs in the scry index

**Reviewing:** `2026-10-25-remote-zellij-design.md` (Draft)
**Reviewer:** Claude (Fable 5.1), time-boxed (~10 min). Claims about existing code
were checked against `src/scry/scry.py`, `src/scry/backends/zellij.py`,
`src/scry/backends/pty_client.py`; zellij behaviour was spot-checked against
zellij 0.45.1 with a throwaway session (`scry-test-fablerev`, deleted afterwards).

## Verdict

The shape is right: fresh ssh per action, remote scry does the zellij work, a
cache that redraws read from disk, verification on the remote before any
keystroke. The claims about the current code are accurate. The findings below
are mostly about (1) a session-identity source that is harder than the design
implies, (2) a cache write design that can lose data despite the "merge per
host" rule, (3) the name-based verification producing false "stale" refusals
after in-zellij renames, and (4) a handful of ssh/remote-shell failure modes
the error mapping doesn't cover yet. None invalidate the design; several should
be settled before the plan is written.

## Code claims verified

- `proxy()` / `_proxy_loop()` release held I/O at `hold_timeout` (default 2.0 s)
  regardless of the gate: correct (`pty_client.py` lines 217-322).
- `_direct_client()` sets the gate in a `finally:` on every path (no client,
  attempts exhausted, `OSError`, generic exception) and only logs a warning:
  correct (`zellij.py` lines 429-480).
- `do_table_loop()` pops only the most recent history entry when it is absent
  from the window list; `attach_window` errors go to `display_error_message`:
  correct (`scry.py` lines 476-499).
- `parse_args_and_configure()` and `backend = get_backend(config)` run at import
  time (`scry.py` lines 189-196). Note the second one too: on a remote whose
  `~/.scry.yml` says `backend: tmux` with no tmux binary installed, import dies
  with exit 1 before any `remote-*` subcommand can emit the "not-zellij" code.
- zellij 0.45.1: `zellij -s <EXITED-session> action list-tabs -j` prints
  "There is no active session!" and exits 1 (it does **not** resurrect), while
  `zellij attach <name>` does resurrect. The design's requirement to check the
  session before spawning `attach` is right, and `_attach`'s existing
  `_list_clients` pre-check would already fail safely on an EXITED session.
- Tab IDs are **not reused within a session** (closed id 1, next new tab got
  id 3) and restart from 0 on a fresh session. This matters for finding 3.
- `_NO_SESSION_MARKERS` / `ZellijCmd` stderr handling exists as described.

## Findings, by severity

### 1. High — The suggested session-identity source doesn't meet its own requirement

`zellij list-sessions --no-formatting` prints a *relative* age
(`main [Created 1day 26m 32s ago]`) with second resolution and unit changes
over time. Converting it to an absolute time gives a value that drifts by a
second or two between calls and is formatted differently once the session is
a day old. It is not "stable otherwise", so a naive builder will produce
spurious "stale" refusals, or round aggressively and lose the restart
discrimination the check exists for.

**Why it matters:** this identity is the primary stale-cache safeguard
(check 1) and the history key; if it is flaky the user learns to ignore
"stale — press z", which erodes the anti-goal.

**Suggest:** name a concrete source in the design rather than leaving it open.
Two that are exact and need no extra tools:
- The zellij **server process**: its cmdline is `zellij --server <socket path>`
  where the path ends in the session name; pid + process start time
  (`/proc/<pid>/stat` field 22) changes on every restart and never otherwise.
  Pure Python, no `ss`/`lsof` needed.
- The server **socket file's inode + ctime** (the socket path is visible in that
  cmdline; the default dir varies by `ZELLIJ_SOCK_DIR`/`XDG_RUNTIME_DIR`, which
  is why discovering it via the cmdline is more robust than guessing).

Either way, make `remote-list` also return a human-readable "session started at"
so the header can show it; cheap and useful for debugging.

### 2. High — "No lock needed; merge per host" still loses writes

Two processes both read the cache, each replaces "its" hosts, each writes. The
second writer's read predates the first writer's write, so it overwrites every
host the first one refreshed. The merge rule only protects hosts the two
writers *didn't both* touch, and only if the reads interleave favourably — a
`z` (all hosts, 5 s) racing a rename (one host, fast) in another of the user's
~15 instances is exactly the losing case. Last-writer-wins is fine for a single
host's data; it is not fine when one writer's stale copy of *other* hosts
clobbers fresh data.

**Suggest the simpler alternative:** one file per host
(`<cache_dir>/<key>.json`), each written atomically via temp+rename. There is
then nothing to merge and no lock; a redraw reads N small files. If a single
file is preferred, take an `fcntl.flock` on a sibling lock file around
read-merge-write; it is three lines, and the design already allows moving the
cache off NFS, which is the only place flock is dubious.

### 3. Medium — Name verification (check 3) produces false "stale" refusals and adds nothing once identity is checked

The user keeps 10-20 tabs per remote and renames tabs inside zellij. With
check 3, every tab renamed on the remote since the last `z` refuses with
"stale — press z" even though the tab ID is correct, the session is the same
incarnation, and (per the probe above) zellij does not reuse tab IDs within a
session. Within one session incarnation, `(identity, tab_id)` already
identifies the tab uniquely; the name adds no protection, only refusals.

The same choice leaks into history: entries keyed on
`(host, id, name, identity)` mean a scry-side `r b2 newname` leaves the old
history tuple unmatched forever (`update_window_history` removes equal entries,
so old and new tuples coexist; `s` can pick the stale one, which then refuses
as "stale" and `z` will never fix it). The restart scenario the design cites
(old tab 7 "logs" vs new tab 7 "build") is already caught by the identity
check alone.

**Suggest:** key remote tabs on `(host, identity, tab_id)` everywhere (cache
lookup, history, highlighting); drop `--expect-name` from attach, or keep it
only to *display* "attached to '<live name>' (was '<cached name>')" rather than
refuse. Keep the name check for **rename** (`r`) if desired, since renaming a
tab whose name has changed under you is a plausible mistake. If the team wants
belt-and-braces anyway, write down that the cost is a forced `z` after every
in-zellij rename, so it is a conscious trade.

### 4. Medium — Remote-shell output can corrupt `remote-list` JSON and exit codes

`ssh host cmd` runs the command through the user's login shell. bash sources
`~/.bashrc` for non-interactive ssh commands; anything it prints lands on
stdout ahead of the JSON. A missing `scry` gives exit 127 (`command not
found`), and an old scry that lacks the subcommand gives argparse's exit 2 with
usage text, both via the same channel as the design's "distinct exit codes".

**Suggest:** frame the JSON (e.g. a sentinel line before/after, or print it as
the final line and parse only that); and extend the exit-code → message table
with 127 ("scry not found on host b; set `scry:` in remotes") and 2
("scry on host b is too old for remote mode"). Also verify, don't assume, what
ssh reports when the remote command dies by signal (OpenSSH reports 255 for
some of these paths), since 255 is mapped to "host unavailable".

### 5. Medium — Attach has no timeout and no documented escape

`remote_timeout` covers `z`/list only. `ssh -t … remote-attach` against a
stopped Coder workspace blocks while `coder ssh` wakes it (tens of seconds to
minutes). Before the pty session is established the local tty is still cooked,
so Ctrl-C kills ssh locally; afterwards Ctrl-C is forwarded to the remote,
where, until remote scry switches to raw mode, it is a SIGINT that kills remote
scry mid-verification (KeyboardInterrupt traceback on screen, generic failure
code). ssh's `~.` escape still works under `-t` and is the practical way out of
a hung attach.

**Suggest:** state the expected user experience (spinner/"connecting to host b…"
line before exec; Ctrl-C and `~.` as escapes), have remote scry install a
SIGINT handler that exits with the generic code, and consider
`ConnectTimeout`/`-o BatchMode=yes` for the non-interactive list/rename calls so
a password prompt can't silently eat the `remote_timeout`.

### 6. Medium — Strict attach needs a success/failure gate, not a boolean, and the final flush leaks the wrong tab

`_proxy_loop` flushes `held_out` unconditionally at loop exit and forwards
`held_in` whenever the gate is set. To satisfy "the client is killed before any
held input is forwarded" and "output stays held", the director must signal
*outcome*, not just completion: on failure, discard both buffers and don't
flush `held_out` on exit (otherwise the user briefly sees the frame of the tab
the client landed on, which is misleading even if harmless). Design should
say this explicitly since it's the first step of the plan and the test
`test_pty_client.py::...hold_timeout elapses` (line 232) will be rewritten
around it.

Also note for the plan: the overall confirm budget is today ~3 s (wait for
client) + 3 × 1 s (confirm) with wake-key sleeps; "longer for the ssh hop" isn't
needed on the remote side (the remote scry talks to a local zellij). What the
remote side needs is **longer `_wait_for_new_client`** only if the Coder
workspace is slow, not a longer confirm.

### 7. Low — ControlPersist contradicts the "no unattended ssh connections" anti-goal, and stale masters hang

A `ControlMaster auto` + `ControlPersist 10m` master is a background ssh
process that outlives the user action. The user may be fine with that, but the
design should say so rather than list it as a recommendation under an anti-goal
that forbids it. Practical hazard: after a workspace auto-stop, a dead master
socket makes the next ssh hang or fail until the mux socket is cleaned
(`ssh -O exit`/`-O check`), which will read as "host unavailable" with no hint.
Mention it in the setup checklist, or suggest `ControlPersist 1m`.

### 8. Low — Selection must resolve against the list as drawn

With 15 instances, one instance's `z` changes positions in the cache while
another still displays the old order. `<key><n>` must resolve against the
remote list captured at that redraw (as local indices do via the in-memory
`windows`), not a fresh cache read. One sentence in the design prevents a
subtle wrong-tab path that the remote verification would catch only by
refusing.

### 9. Low — Terminal-restore list is incomplete for zellij

zellij 0.45 also enables SGR mouse (`?1006`), any-motion/button-event mouse
(`?1002`/`?1003`), focus events (`?1004`) and may push kitty keyboard-protocol
flags (`CSI > 1 u`, pop with `CSI < u`). Add those to the reset sequence.

### 10. Low — Minor spec gaps a builder will trip on

- `ensure_session_group_exists` runs when the *local* list is empty and
  `continue`s before drawing; remote sections would be invisible in that state.
  Harmless under tmux (a new session has a window) but worth a note.
- `process_command` uses `command.isdecimal()`; `<key><n>` needs a regex that
  rejects `b` alone, and `r b2 name` needs a branch in
  `process_rename_window_command` which currently assumes an integer index.
- `#` for a remote tab the user is currently attached to from *this* terminal
  can't be shown live (no refresh on detach in v1); say so in the header help
  so it isn't reported as a bug.
- Timed-out `z` subprocesses must be killed as a process group
  (`start_new_session=True` + `killpg`) or the `coder ssh --stdio`
  ProxyCommand child lingers.
- `remote-list` on the remote should disable the stderr `ERROR` handler (or
  send it to the log file); with `-t` it is on the tty, without `-t` it is noise
  the local side must ignore.

## Over-engineering / simplifications

- Per-host cache files remove the merge rule, the atomic-merge test cases, and
  the race (finding 2).
- Dropping `--expect-name` from attach removes a flag, a verification step, a
  history field, and a class of false refusals (finding 3).
- Protocol versioning: a single integer in the JSON plus a `--proto N` flag is
  enough; do not build negotiation.
- Rename is correctly flagged as first to cut; agreed.

## What the design gets right (keep)

- Fresh ssh per action, remote-side verification before any keystroke, no
  background refresh, cache-only redraws: each directly serves a stated goal.
- Checking the session before `attach` (resurrection hazard) is correct and
  verified against 0.45.1.
- Requiring explicit `zellij_wake_keys` on the remote is the right call given
  the wrong-tab anti-goal.
- Making strict attach a standalone first step with its own tests.
- Deferring piggyback refresh and explicitly forbidding interim
  refresh-on-return that it would obsolete.
