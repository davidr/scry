# Design: Remote zellij tabs in the scry index

**Date:** 2026-10-25
**Status:** Draft, revised after the Fable and Astra reviews and the second
Fable review (decisions from each synthesis are folded in). Approved for planning.

Related: `2026-10-25-remote-zellij-design-fable-review.md`,
`2026-10-25-remote-zellij-design-astra-review.md`,
`2026-10-25-remote-zellij-design-fable-review-2.md`,
`2026-10-25-zellij-attach-hardening-notes.md`.

## Problem

The user keeps long-lived zellij sessions (10–20 tabs each) on two or three
remote hosts. Today they reach them by attaching to a local scry window, running
`ssh` in it, and attaching to the remote zellij there. The remote tabs are
invisible to scry's index and can't be selected directly; the user has to go
through the ssh window and navigate the remote zellij by hand. As a side effect,
remote zellij runs *nested* inside the local multiplexer, and both use the same
tmux-style `Ctrl-a` prefix. A direct attach removes that nesting.

## Goal

Every tab of each configured remote zellij session appears in the local scry
index, and selecting one puts the user's terminal directly on that remote tab.
This works identically whether the local scry runs the tmux or zellij backend.
Detaching from the remote tab returns the user to the local scry prompt.

## Guiding principle

scry is a single-user tool, and this feature is **best-effort, not
bulletproof** (user's words: "this really doesn't need to be absolutely
bulletproof"). Fix what the user is remotely likely to hit, or what costs
nothing; document rare races as known limitations rather than design around
them.

## Non-goals (first version)

- Creating remote tabs (`n`). Planned for later.
- Renaming remote tabs (`r`). Cut from v1 (see "Decided against").
- Remote multiplexers other than zellij. The remote side **must** be zellij.
- Reusing or steering the user's existing long-lived ssh windows. They are left
  alone; scry does not know about them.
- `d` / `l` (dump/load) covering remote tabs. They stay local-only.
- Any automatic, background, or age-based refresh of remote data.
- Fixing remote terminfo / `TERM` problems (documented as a setup check only).
- Making zellij attach strictly safe against concurrent clients (zellij can't
  target a client; see "Attach safety").

## Anti-goals

- **Typing into the wrong remote tab because the cache was stale.** Stale-cache
  cases (session restarted, tab closed) must be caught by verification on the
  remote before attaching, and refuse with a clear message. Rare concurrency
  races inherent to zellij are accepted and documented (see "Known
  limitations").
- **Slowing down index redraws.** The user runs ~15 scry instances across
  multiple terminal emulators and redraws constantly. A redraw must never touch
  the network.
- **scry opening ssh connections on its own.** scry only connects as the direct
  result of a user action (`z`, attaching). No timers, no refresh-on-redraw, no
  refresh-on-detach in this version. This is about what *scry* initiates: an
  ssh master the user configures themselves (`ControlPersist`) is the user's
  choice and is fine.

## Environment and constraints

- Remote hosts are Coder workspaces reached through a per-host `ProxyCommand`
  (`coder ssh --stdio …`), typically as `coder.<name>`. scry just runs `ssh
  <host>`; whatever the user's ssh config does applies.
- Workspaces auto-stop overnight; home directories persist. Zellij sessions are
  restarted about once a week or on demand. **A restart renumbers tab IDs**, so
  a cached tab ID can point at a different tab after a restart. Within one
  session, zellij does not reuse tab IDs (verified on 0.45.1 by the Fable
  review).
- The user rarely opens, closes, or moves remote tabs.
- scry is installed on each remote with `uv tool install` (lands in
  `~/.local/bin`, which survives workspace stops). Neither scry nor zellij may
  be on `PATH` for non-interactive ssh commands.
- The user's home directory may be on NFS; the cache location must be
  configurable.
- The remote zellij requires the same >= 0.45.1 as the existing zellij backend.

## Decisions

### Mechanism: a fresh ssh connection per action, remote scry does the work

Selecting a remote tab makes the local scry print `connecting to host b…` and
run, in its own terminal (foreground, inheriting the tty — the same place it
would run a local attach):

```
ssh -t <host> <scry> remote-attach --tab=<id> --expect-session=<identity> --proto=<N>
```

The **remote scry** does the zellij work using the existing `ZellijBackend`
attach path (pty-owned client, wake keys, `go-to-tab-by-id`, 2 s input/output
hold). When the user detaches, the remote client exits, ssh exits, and the local
scry restores the terminal and redraws its index.

Listing is a non-interactive remote scry invocation (`remote-list`, see
"Refresh").

Exact subcommand/flag spelling is the builder's call; the operations and their
arguments are not. Arguments are passed in `--opt=value` form so a value can't
be parsed as an option.

**Remote dispatch must be headless:** the `remote-*` subcommands must never
enter `do_table_loop`, never create or resurrect a session (no
`ensure_session_group`), and must validate the remote config (backend is
zellij, wake keys set) before constructing a backend. Note that today both
`parse_args_and_configure()` and `backend = get_backend(config)` run at import
time of `scry.py`, and `run_scry()` always enters the UI; the builder has to
route around that.

### Configuration

**Local `~/.scry.yml`** gains a `remotes` list. Being listed there is how scry
learns a host is meant to have a remote zellij session:

```yaml
remotes:
  - key: b                    # single letter; indexes become b0, b1, …
    label: build box          # shown in the section header
    host: coder.build         # passed to ssh as the destination
  - key: c
    label: gpu box
    host: coder.gpu
    scry: ~/.local/bin/scry   # optional; default "scry"
remote_cache_dir: /var/tmp/davidr-scry   # optional
remote_timeout: 10            # optional; seconds per host for z
remote_ctrl_c_guard: true     # optional; see "Ctrl-C handling"
```

- `key` must be a single ASCII letter, unique across remotes. Duplicate or
  invalid keys are a startup error (clear message, no traceback, like an invalid
  backend). A key that equals a bare command letter (`s`, `q`, `z`, `u`, `d`,
  `l`, `n`, `r`) is allowed — remote selections always end in a digit, so they
  can't collide — but scry logs a warning.
- `host` is validated as an ssh destination: it must not start with `-`.
- `scry` path policy: either an absolute remote path, or a path starting with
  `~/`. For `~/`, only that prefix is expanded **on the remote** (sent as
  `"$HOME"/<shell-quoted rest>`); everything else is shell-quoted with
  `shlex.quote`. Never expanded locally (local and remote homes may differ).
- `remote_cache_dir` default: `$XDG_CACHE_HOME/scry/remotes/`, falling back to
  `~/.cache/scry/remotes/`. The user can point it off NFS. *(Default location
  is my call.)*
- `remote_timeout` default 10 s, covering the whole refresh of one host
  (connection, ProxyCommand, remote command). A cold `coder ssh` handshake plus
  remote shell, Python startup, and two zellij calls can exceed 5 s without a
  live ControlMaster, which would intermittently mark healthy hosts unavailable
  (second Fable review). The user confirmed a few seconds of blocking is fine.
- `remote_ctrl_c_guard` default `true`: enables scry's Ctrl-C handling around
  remote ssh calls (see "Ctrl-C handling"). Set `false` to fall back to plain
  default signal behavior if the handling ever causes trouble (user request).

**Remote `~/.scry.yml`** (on each remote host) is the authority on that end:

```yaml
backend: zellij
session_group: main          # the long-lived zellij session's name
zellij_wake_keys: "\x01."    # must be a no-op under THIS host's keybindings
zellij_bin: ~/.cargo/bin/zellij   # optional; for when zellij isn't on PATH
```

- New key `zellij_bin` (optional, both local and remote): used instead of the
  `PATH` lookup in `ZellijBackend.__init__` when set. Needed because
  non-interactive ssh shells may not have zellij's install dir on `PATH`.
- The remote operations **refuse** (distinct exit code) if the remote config's
  backend is not `zellij`, or if `zellij_wake_keys` is not explicitly set in the
  remote's config file. This enforces "remote must be zellij" and makes the
  wake-key hazard a setup step the user can't skip: the default `\x07\x07` may
  not be a no-op under the remote's keybindings and would type into a pane.
  *(Requiring explicit wake keys is my call.)*

### Session identity

Each remote session instance is identified by the **zellij server process's PID
plus its start time** (`/proc/<pid>/stat` field 22). The server's cmdline is
`zellij --server <socket path>`, where the socket path ends in the session name;
that's how remote scry finds the server for `session_group`. This changes on
every restart and never otherwise. Verified on 0.45.1 (second Fable review).

- Match the server **exactly**: `argv[1] == "--server"` and
  `basename(argv[2]) == session_group`. Don't match on `argv[0]` (it varies with
  install path), and never use a substring match (`pgrep -f` style): the user's
  own `zellij attach main` clients and shells mentioning the name would match,
  tripping "ambiguous → fail closed" for no real reason.
- Parse `/proc/<pid>/stat` **after the last `)`**, since the `comm` field can
  contain spaces and parentheses; the start time is field 22 overall.

- If the identity can't be determined (no matching server, unreadable `/proc`,
  ambiguous match), remote scry **fails closed**: list reports an error state for
  that host, attach refuses.
- `zellij list-sessions`' "Created … ago" text is **not** usable: it's relative,
  drifts, and changes format with age (both reviews).
- `remote-list` also returns a human-readable "session started at" for
  debugging. *(Fable suggestion; cheap.)*
- Linux-only, which matches the remote hosts.

### Remote tab identity

A remote tab is identified everywhere (cache lookups, history, highlighting,
attach) by **(host, session identity, tab ID)**. Because zellij doesn't reuse tab
IDs within a session, that's unique. The tab name is used for display only. An
in-zellij rename since the last `z` therefore does not cause a refusal; the
index just shows the old name until the next `z`.

### Cache

- **One cache file per remote**, in `remote_cache_dir`, named after the remote's
  `host` (sanitized for the filesystem), not its letter. Reassigning a letter to
  a different host makes scry read a different file, so the old one is ignored
  rather than misread. *(Naming by host is my call; the reviews' durable
  endpoint-identity proposal was judged overkill for a single user.)*
- Each file holds that host's tab list (tab ID, name, active client count), the
  session name and identity, the time of the last successful refresh, and the
  result of the last attempt (ok / unavailable / no session / error + reason).
- Writes are atomic: write a temp file in the same directory, then rename it
  over the host's file. With one file per host and `z` as the only writer, there
  is nothing to merge and no lock is needed. Last-writer-wins per host is fine.
- Reads tolerate a missing, empty, or corrupt file (treat as "never
  refreshed"; never crash a redraw).
- When a host fails or times out during a refresh, its **last known tabs are
  kept** and the host is marked unavailable. When it reports **no session**,
  its tabs are **cleared** (see below).

### Refresh

- New command **`z`**: refresh all remotes **in parallel**. It **blocks** at the
  prompt until every host answers or hits `remote_timeout` (user confirmed a few
  seconds of blocking is fine), then writes each host's cache file and redraws.
- That is the only refresh trigger in this version.
- Each refresh runs `ssh -T -o BatchMode=yes <host> <scry> remote-list
  --proto=<N>`:
  - stdin is `/dev/null`; stdout and stderr are captured separately. Never
    disable host-key checking.
  - `BatchMode` stops parallel workers from fighting over password, passphrase,
    or host-key prompts while scry owns the UI. A host that needs interactive
    authentication fails with a message telling the user to `ssh <host>` once by
    hand. (A Coder ProxyCommand isn't bound by `BatchMode`; whatever it does is
    on the user's ssh config.)
  - The process runs in its own process group (`start_new_session=True`). On
    timeout the whole group is killed (`killpg`), so the `coder ssh --stdio`
    ProxyCommand child doesn't linger. **Builder to verify on a real host:**
    whether a `ControlPersist` master spawned by that same connection stays in
    the group (and so gets killed by a timed-out `z`, losing the speedup for the
    next one). If it does, find a way to spare it; if not, nothing to do.
  - Ctrl-C during a blocking `z`: see "Ctrl-C handling".
- **Framing:** remote scry prints the JSON between explicit begin/end sentinel
  lines. Anything outside the sentinels (e.g. text printed by the remote's
  `~/.bashrc`) is ignored. Missing sentinels, invalid JSON between them, or a
  response over a size cap (e.g. 1 MB) is a protocol failure with a useful
  diagnostic; scry never tries to dig JSON out of unframed output.
- Remote scry's stderr `ERROR` logging handler is disabled (or sent to its log
  file) for `remote-*` subcommands, so it doesn't pollute output.

### Remote session not running

If the remote's `session_group` session doesn't exist or is EXITED (Coder
workspace restart, session quit), scry **refuses and never creates or
resurrects it** (user decided: they start it themselves).

- `remote-list` reports a distinct "no session" state. The cache records it and
  **clears that host's tabs** (they're definitively gone, and any IDs would be
  new after a restart). Header: "b · build box · no session running — start it,
  then press z". *(Clearing rather than keeping dimmed tabs is my call.)*
- `remote-attach` exits with a distinct "no session" code → "no zellij session
  running on host b".
- The remote attach checks that the session is running *before* spawning
  `zellij attach`, because `zellij attach <name>` resurrects an EXITED
  (serialized) session. The Fable review confirmed that `action` commands on an
  EXITED session fail rather than resurrect, so `_attach`'s existing
  `_list_clients`/`_list_tabs` snapshot already fails safely.

### Verification before attach

Before attaching, the remote scry checks, against live state:

1. The current session identity equals `--expect-session`. Otherwise: refuse,
   "stale" → "host b's tab list is stale — press z".
2. A tab with `--tab=<id>` exists. Otherwise: refuse, "tab gone". (The existing
   `_attach` already raises "window <id> no longer exists".)

Then the existing attach path runs, under the attach lock (next section).

### Attach safety (best effort)

User decided: attach is best-effort. Today's behavior stays: input and output
are held until the director confirms the client is on the tab **or 2 s pass**,
whichever comes first. There is no strict attach. The full analysis, the signals
that it has become a problem, and the options for hardening it later are in
`2026-10-25-zellij-attach-hardening-notes.md`.

**One addition, the attach lock**, in `ZellijBackend._attach` so it covers
**local and remote** zellij attach (user decided: one code path):

- `fcntl.flock` on `scry-attach-<session>.lock` in `$XDG_RUNTIME_DIR` (fallback
  `/tmp`): local disk, never NFS. Released by the OS if the process dies, so no
  stale locks.
- Held only for the **steering phase**: taken just before spawning the client,
  released when the director finishes (confirmed, gave up, or errored). Not held
  while the user works in the tab.
- If busy, wait up to a few seconds, then proceed without it and log a warning.
- This removes scry-vs-scry races (two scry instances attaching to the same
  session at once). It does not cover manual attaches or typing in other clients.

### Index layout (user chose layout C)

```
0) mail ........... 4) ssh-c ..........
1)#scry ........... 5) music ..........
2) notes .......... 6) dotfiles .......
3) ssh-b .......... 7) scratch ........

── b · build box ──────────── 4m ago
b0) api ........... b2) db ............
b1) build ......... b3)#logs ..........

── c · gpu box ── unavailable · 2d ago
c0)  jupyter ....... c1)  train .......

0-7/b0-b3/n/r/s/z/q/?:
```

- Local windows first, numbered exactly as today.
- One section per remote, in config order. Header: key, label, and age of the
  last successful refresh ("4m ago"); or "unavailable · <age of last success>"
  when the last attempt failed; or the no-session text; or "never refreshed —
  press z".
- Remote tabs are indexed `<key><n>`, with tabs **sorted by name** (user
  decided), matching the local list's sort; ties broken by tab ID. Indices never
  shift when local windows change.
- Unavailable hosts: header and tabs rendered dim. Their tabs are still
  selectable — the attempt simply runs and reports whatever happens.
- `#` comes from the cached active-client count (as fresh as the last refresh;
  it can't reflect this terminal's own attach until the next `z` — the `?` help
  says so).
- The existing magenta/green/blue history highlights apply to remote tabs too.
- Remote sections use the same multi-column, column-width, name-elision, and
  `hide_prefixes` rules as the local list. *(My call.)*
- **Names, labels, and remote diagnostic text are rendered as literal text**,
  not Rich markup (e.g. `rich.markup.escape`), with control characters
  (newlines, escapes) replaced for display. A tab named `[/]logs` must not break
  the redraw. This also fixes the same existing bug for local window names.
- Edge case: `do_table_loop` currently `continue`s before drawing when the local
  window list is empty (`ensure_session_group_exists`); the builder should make
  sure remote sections still show in that state or note why not.
- The prompt's option hint includes the remote index ranges and `z`; `?` help
  documents `<key><n>` and `z`.

### Commands

- `<key><n>` (e.g. `b2`): attach to that remote tab. Parsed as one letter
  followed by digits (`b` alone is not a selection).
- `z`: refresh all remotes (blocking, parallel).
- Unknown key or out-of-range index: "Invalid index", like local.
- **Selection resolves against the snapshot that was drawn.** Each loop
  iteration captures the remote rows (host, session identity, tab ID, name) once
  and uses that same snapshot to draw and to resolve `b2`, exactly as local
  windows already flow from `list_windows` into `process_command`. A `z` in
  another scry instance only affects this instance's next redraw.

### History

- Remote entries in `WINDOW_HISTORY` are **(host, session identity, tab ID)**,
  so they can't collide with local tab/window IDs (zellij local tab IDs are also
  small integers) and can't outlive a session restart.
- `s` (swap) and Enter-on-empty (re-attach most recent) work across local and
  remote entries, passing the entry's own recorded session identity as the
  expectation.
- `do_table_loop`'s history cleanup — which pops the most recent entry if it's
  no longer in the window list — treats a remote entry as present only if the
  snapshot has a tab with the same host, identity, and tab ID.

### Attach user experience and escapes

- Before running ssh, scry prints `connecting to host b…` so a slow connection
  (e.g. Coder waking a stopped workspace) isn't a mystery blank screen.
- Ways out of a hung attach: Ctrl-C while ssh is still connecting (the local tty
  is cooked then), and ssh's `~.` escape at any time. The `?` help and README
  mention both.
- Remote scry installs a SIGINT handler for `remote-attach` that exits cleanly
  with the generic failure code instead of printing a traceback.

### Ctrl-C handling

Problem (second Fable review, confirmed): scry has no SIGINT/`KeyboardInterrupt`
handling anywhere. The foreground `ssh -t` shares scry's process group, so
Ctrl-C while ssh is connecting — the documented escape from a slow Coder wake —
also kills the local scry (traceback, history lost, possibly the terminal window
closed). Ctrl-C during a blocking `z` kills scry too (its workers are in their
own process groups, so only scry gets the signal).

When `remote_ctrl_c_guard` is true (default):

- **Attach:** while the foreground ssh runs, scry installs a **no-op Python
  SIGINT handler** (not `SIG_IGN`) and restores the previous handler in a
  `finally`. A caught signal's disposition resets to default across `exec`, so
  ssh still receives and dies on Ctrl-C while scry keeps waiting. scry then
  shows "connection to host b cancelled" (ssh's exit status after SIGINT) and
  redraws. **Do not use `SIG_IGN`:** ignored dispositions survive `exec`
  (`subprocess`'s `restore_signals` only resets SIGPIPE/SIGXFZ/SIGXFSZ), and ssh
  honours an inherited ignore for SIGINT, so the escape would stop working.
  Once attached, the remote side has the tty in raw mode, so Ctrl-C is just a
  byte to the remote session, as today.
- **`z`:** catch `KeyboardInterrupt` around the wait, `killpg` every worker,
  keep the existing cache files untouched, and redraw with "refresh cancelled".

When `remote_ctrl_c_guard` is false: no handler is installed and no
`KeyboardInterrupt` is caught around remote calls; behavior is Python's default
(Ctrl-C ends scry), i.e. an escape hatch in case the guard ever breaks something.

### Errors

- Remote failures can't be printed through `ssh -t` (the next redraw wipes
  them). The remote scry exits with **distinct exit codes** — at least: stale,
  tab gone, no session running, not-zellij / wake keys unset, identity
  unavailable, protocol mismatch, generic failure. The local scry maps them to a
  message shown at its prompt (the same slot `do_table_loop` already uses for
  "window <id> no longer exists").
- Codes from outside scry also get messages:
  - 255 (ssh failed: unreachable, auth) → "host b unavailable".
  - 127 (command not found) → "scry not found on host b; set `scry:` in
    remotes".
  - 2 (argparse usage error) → "scry on host b is too old for remote mode".
- scry's own codes must not collide with these. Exact values are the builder's
  call. The builder should check what ssh reports when the remote command dies
  by a signal, since 255 means "unavailable".

### Protocol versioning

A single integer. `remote-list` includes it in its JSON; the local scry passes
`--proto=<N>` to both remote operations. On mismatch, the side that notices
reports "scry on host b is incompatible — upgrade it" (protocol-mismatch exit
code, or an error field in the list JSON). No negotiation.

### Terminal restore

Before running a remote attach's ssh, the local scry saves the terminal's
termios settings. After ssh exits — normally or not — it **restores those saved
settings** (not a generic "sane" preset) and writes a reset sequence before
redrawing: leave the alternate screen (`?1049l`), show the cursor (`?25h`),
disable mouse reporting (`?1000l`, `?1002l`, `?1003l`, `?1006l`), focus events
(`?1004l`), and bracketed paste (`?2004l`), and pop kitty keyboard-protocol
flags (`CSI < u`). A dropped connection otherwise leaves the remote zellij's
terminal modes active locally.

## Known limitations (accepted)

- **zellij can't target a client.** A keystroke in another client of the same
  session at the exact moment scry steers its new client can misroute: the other
  client may be moved to the target tab, or scry may confirm the wrong client.
  The attach lock removes scry-vs-scry cases; manual attaches and typing in other
  clients remain. Rare for one user at one keyboard.
- **Steering failure releases after 2 s.** If direction fails (including zellij
  0.45.1 client-ID reuse), the user lands on whichever tab the client opened on,
  and keys typed during the blank 2 s hold go there. The user will see the wrong
  screen. Hardening options: see the attach hardening notes.
- **Resurrection race.** If the session exits in the instant between remote
  scry's "is it running?" check and `zellij attach`, zellij resurrects it. The
  window is milliseconds, and the result is a session the user would start
  anyway.
- **A hung zellij command during steering** can stall scry when the user
  detaches (unbounded `director.join()`), as it can today.
- **`#` and names are as fresh as the last `z`.**

## Decided against

- **Remote rename (`r <key><n>`).** A restart between verification and
  `rename-tab-by-id` could rename a different tab, and a dropped connection
  leaves the outcome unknown. The user is fine without rename.
- **Strict attach.** Analysed in the hardening notes; the user chose best-effort.
- **Name check on attach.** Session identity plus tab ID is already unique; a
  name check only adds false "stale" refusals after renames inside zellij.
- **Background, age-based, or refresh-on-return refresh.** Would mean unattended
  connections across ~15 instances; refresh-on-return would also be obsoleted by
  the piggyback refresh.
- **Durable endpoint/configuration identities.** Overkill for one user editing
  their own config; naming cache files by host covers the realistic case.

## Setup checklist (goes in the README)

1. `uv tool install` scry on each remote.
2. Remote `~/.scry.yml` with `backend: zellij`, `session_group`, and
   `zellij_wake_keys` that are a no-op under that host's keybindings; add
   `zellij_bin` if zellij isn't on the non-interactive `PATH`.
3. Local `~/.scry.yml` `remotes:` entries; set `scry:` (absolute or `~/…`) if
   `ssh <host> scry` doesn't find it.
4. Run `ssh <host>` once by hand so host keys and authentication are settled
   (refresh uses `BatchMode` and won't prompt).
5. Recommended: ssh connection sharing for the remote hosts, in a block
   **outside** the Coder-managed `coder config-ssh` section:
   ```
   Host coder.*
       ControlMaster auto
       ControlPath ~/.ssh/cm/%C
       ControlPersist 10m
       ServerAliveInterval 30
   ```
   (`mkdir -p ~/.ssh/cm && chmod 700 ~/.ssh/cm`.) This skips the `coder ssh`
   ProxyCommand on reused connections, making `z` and attach much faster. The
   master is a background ssh process you chose to configure; scry itself never
   starts connections on its own. After a workspace stops, a dead master can make
   the next connection hang or fail ("host unavailable"); `ssh -O check <host>`
   shows it, and `ssh -O exit <host>` clears it.
6. Check that the remote knows the local terminal's `TERM` (terminfo); if a
   terminal emulator's `TERM` is unknown remotely, zellij may render badly.
7. Verify with the exact non-interactive command scry will run:
   `ssh -T -o BatchMode=yes <host> <scry> remote-list` should print framed JSON.

## Testing

- Unit tests with ssh faked (subprocess patched): config validation (keys, host,
  path policy), per-host cache read/write/corruption, index rendering of remote
  sections (unavailable, no session, never refreshed, name-sorted order),
  command parsing (`b2`, `b`, `z`), snapshot-bound selection (rewrite the cache
  between drawing and handling the command), history entries and cleanup,
  exit-code → message mapping (including 127, 2, 255), framing (noise outside
  sentinels, missing sentinels, oversize), remote command quoting (`~/` prefix,
  awkward values), and literal rendering of awkward names (brackets, newlines,
  escapes, Unicode, leading dashes) — local and remote.
- Remote-side unit tests: session identity discovery (found, missing,
  ambiguous → fail closed), verification (stale identity, tab gone, no session /
  EXITED session, not-zellij, wake keys unset, protocol mismatch), headless
  dispatch (never enters the UI or creates a session), against a faked zellij
  and faked `/proc`.
- Attach lock: taken before the client spawns, released when the director
  finishes, a second attach waits, a busy lock times out and proceeds with a
  warning.
- Terminal restore: saved termios restored and reset sequence written on normal
  exit, ssh failure, and connection loss.
- Ctrl-C guard: during a foreground ssh, SIGINT doesn't end scry and the child
  still dies on it (use a real child process, not a mock, to prove the handler
  isn't inherited as an ignore); the previous handler is restored afterwards;
  Ctrl-C during `z` kills the workers, leaves caches unchanged, and shows
  "refresh cancelled"; with `remote_ctrl_c_guard: false`, none of this is
  installed.
- Session identity discovery: exact `--server` match ignores client processes
  and shells mentioning the session name; `/proc/<pid>/stat` parsing handles a
  `comm` with spaces/parentheses.
- Opt-in live tests using `ssh localhost` as a stand-in remote (a real ssh
  subprocess, so `BatchMode`/`-T`/framing behave for real), gated like the
  existing `SCRY_LIVE_ZELLIJ=1` live tests, using throwaway session names (never
  the real `main`). Include identity stability across repeated lists and change
  across a restart.

## Planned second step: piggyback refresh (not in this version)

Return the fresh tab list over the attach connection itself, so every detach
refreshes that host's cache with zero extra connections:

- After the remote zellij client exits and the remote terminal is restored, the
  remote scry writes one framed block (an escape sequence terminals ignore,
  wrapping the tab list JSON) and exits.
- The local scry runs ssh on a pty it owns and relays it (generalizing
  `PtyClient`/`proxy` beyond zellij so it works under the tmux backend too),
  watching for the frame across read boundaries, stripping it so it never
  reaches the screen, and writing it into the cache.
- If nothing arrives (crash, dropped connection), the cache is unchanged.
- **Open issue before building it:** the frame shares a byte stream with
  arbitrary pane output, so a pane could print a matching frame (Astra review).
  Framing, provenance (e.g. a per-attach nonce, accepted only after the client
  exits), size limits, and interruption behaviour need designing first.

The first version should not build anything that this would make obsolete
(i.e. no debounced refresh-on-return).

## Left to the builder

- Subcommand/flag names for the remote operations and how they're wired around
  the import-time argparse/backend setup.
- Exact exit-code values, JSON field names, sentinel strings, per-host cache file
  schema and filename sanitization.
- Module layout (e.g. a `remote.py` alongside `backends/`).
- Attach-lock wait duration.
