# Sketch: zellij session-per-window mode

**Status:** not implemented, deliberately deferred (2026-10-25). This is a rough design for a
future agent to pick up if the current tab-per-window zellij backend proves unreliable.

## Why this exists

The current zellij backend (`src/scry/backends/zellij.py`) mirrors tmux: one zellij session per
session group, one tab per scry window. tmux makes that work through session groups, which give
every attached terminal its own addressable "current window". zellij has per-client tab focus but
**no CLI way to say "move client X to tab Y"**: `go-to-tab-by-id` goes to whichever client
zellij considers most recently active (undocumented). Nearly all the complexity in the backend
exists to work around that one gap:

- `PtyClient` (`pty_client.py`): scry owns the client's pty and holds I/O until the client is on
  the right tab
- wake keys (`zellij_wake_keys`): fake keypresses that make the new client the action target
- the director thread: snapshot, `go-to-tab-by-id`, confirm, retry, and put back displaced clients
- headless helper clients and tab-size verification (zellij bug #5594: tabs created with zero
  clients attached are 0x0)

In session-per-window mode **each scry window is its own zellij session**. Attaching is plain
`zellij attach <name>`, which zellij targets exactly, so none of the above is needed.

## Tradeoff (measured, zellij 0.45.1, server PSS only)

| Design | Per window | 100 windows |
|---|---|---|
| Tab-per-window (current) | ~3.4 MB per tab, on a ~46 MB base | ~0.4 GB |
| Session-per-window | ~30 MB per session (with 20 running) | ~3 GB (extrapolated) |

Shells and programs inside each window cost the same either way, so they aren't counted. The
user often has 100+ windows open, which is why this was deferred. Switch only if the routing
workarounds cause real trouble.

## Spike findings (all verified against zellij 0.45.1)

- `zellij attach -b NAME` (`--create-background`) creates a detached session in about 0.06 s.
  Running it again on an existing name exits 1 with `Session already exists`.
- **No #5594 problem for new sessions:** a headless session's first tab starts at 49x50, resizes
  to the first client (e.g. 39x120), keeps its tab ID, and survives detach. No helper client
  needed.
- `zellij -s NAME action list-clients` works with no client attached. It prints a header line
  plus one row per client, which is enough for the `#` column.
- `zellij -s NAME action rename-session NEW` works from outside the session with no client
  attached. The old name then fails with `Session '…' not found`, which `ZellijCmd` already
  treats as an error despite exit 0.
- `zellij attach -b NAME -- cmd args…` runs an initial command, and its output is visible on
  first attach.
- `list-tabs -j` works headless.
- **Pitfall:** actions that need a client do nothing with no client attached, and still exit 0.
  Headless `write-chars` input is dropped; `dump-screen` writes no file. The backend must not
  rely on these. Use the initial command passed to `attach -b` instead.
- CLI latency: `list-sessions`, `list-tabs`, and `list-clients` each take about 10–20 ms.

## Shape of the implementation

A new class `ZellijSessionsBackend(MuxBackend)`, probably in `src/scry/backends/zellij_sessions.py`,
reusing `ZellijCmd` and `_NO_SESSION_MARKERS` from `zellij.py`. The tab-mode backend stays as is;
the interface means `scry.py` needs no changes beyond config. Estimated size: ~120–180 lines of
backend, ~150–200 lines of unit tests, ~50–80 lines of live tests.

Selection: either a third backend name (`--backend zellij-sessions`, the least code: one factory
entry plus an argparse choice) or a `zellij_mode: tabs|sessions` config key. Either way, update
`get_backend()` in `backends/__init__.py`, the config defaults/argparse in `scry.py`,
`test_backends_factory.py`, README, and CLAUDE.md.

### Session naming (the key decision)

scry needs a stable `window_id`, but renaming a session changes its name. Proposal: encode
everything in the session name so that **one `list-sessions` call yields the whole window list**:

```
<session_group>.<stable_id>.<display name>
```

e.g. `main.00042.build-logs`. `window_id` = `<stable_id>`, `window_name` = display name, and
rename = `rename-session` to the same group and ID with a new display name. Check zellij's
session-name restrictions (allowed characters, max length, since names become socket paths)
and validate or escape display names. An alternative is to keep the session name fixed and use
tab 0's name as the display name, but that costs one `list-tabs` call per session per redraw.

### Method mapping

| `MuxBackend` method | Implementation |
|---|---|
| `list_windows(group)` | `list-sessions -n`, skip EXITED, keep the `<group>.` prefix, parse ID and name. `window_active_clients` comes from `list-clients` per session: at 100+ windows, run those calls in parallel (a thread pool) or a serial loop costs 1–2 s per redraw |
| `list_sessions()` | Report each group (distinct prefix) as a session, so `ensure_session_group`/`session_exists` keep working |
| `create_detached_window(name, group)` | Pick the next free stable ID, then `attach -b <group>.<id>.<name>`. Raise if the name already exists in the group |
| `rename_window(id, new, group)` | Find the session by ID, `rename-session`, then re-list to verify (zellij exit codes aren't trustworthy) |
| `attach_window(id, group)` | Find the session by ID (raise "window <id> no longer exists" if it's gone), then run `zellij attach <name> options --on-force-close detach` in the foreground. No pty proxy |
| `ensure_session_group(group)` | No-op, or create a first window if scry expects the group to be non-empty (check `do_table_loop`'s startup path) |
| `creating_windows(group)` | Default `nullcontext()`; no helper needed |

### Open questions

1. EXITED (resurrectable) sessions: hide them (simplest, matches current `list_sessions`), or
   show them as windows that can be restored?
2. Is the `#` column worth the per-session `list-clients` calls at 100+ windows, or should this
   mode drop it?
3. Does closing a window from inside zellij (last pane exits) kill the session cleanly, or leave
   an EXITED entry that piles up? The user may want serialization turned off for scry-managed
   sessions (`zellij options --session-serialization false`, or per session via config).
4. Should `zellij_wake_keys` be ignored or rejected in this mode?

## Testing notes

- Never test against the user's real `main` group. Use throwaway names (`scry-test-<pid>`,
  `scry-spike-<pid>-*`) and always `kill-session` + `delete-session --force` in a `finally`.
- Attach a test client on a pty with a set size (`pty.fork()` + `TIOCSWINSZ`) and use
  `list-tabs -j` `viewport_rows`/`viewport_columns` to check sizing. `dump-screen --path` only
  works while a client is attached.
- Measure memory with `Pss` from `/proc/<pid>/smaps_rollup` on the `zellij --server` processes.
  RSS overcounts shared pages by about 50%.
- Live tests go behind the existing `SCRY_LIVE_ZELLIJ=1` / `-m live` gate
  (see `tests/test_zellij_live.py`).
