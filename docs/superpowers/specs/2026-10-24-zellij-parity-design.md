# Zellij Backend Parity — Design

**Date:** 2026-10-24
**Status:** Approved in conversation (decisions below were made by David)
**Supersedes:** the "Zellij backend semantics" and "Spike results" sections of
`2026-07-06-mux-backend-design.md`, whose conclusions were partly wrong.

## Goal

Under `--backend zellij`, scry behaves like the tmux backend:

- Attaching from a terminal lands *that* terminal on the chosen window and moves no
  other attached terminal.
- Creating windows (`n`, `l`) moves no attached terminal, and the windows survive.
- Rename (`r`) works.
- The `#` indicator shows windows that some client is viewing.
- Window identity survives renames (history highlighting keyed on a stable ID).

Constraint: zellij **0.45.1** is the target. No zellij plugins.

## Verified zellij 0.45.1 facts (tested 2026-10-24, throwaway sessions)

1. **Remote action routing.** `zellij --session S action …` sent from outside the
   session goes to the client that most recently received *key input*; if no client has
   received any, the first-attached client. Attaching does not count as input.
   `ZELLIJ_PANE_ID` / `ZELLIJ` / `ZELLIJ_SESSION_NAME` env vars are ignored for routing.
   Focus events (`ESC [ I`), SGR mouse motion, empty bracketed paste, F20, and terminal
   resizes do **not** count as input. Ordinary key presses (Ctrl-g; Ctrl-a then Esc)
   do, whether zellij consumes them or passes them to the pane. (Rechecked for focus-in
   and focus-out+in under both stock and tmux-style keybindings: never routes, never
   reaches the pane.)
2. **New-client landing.** A newly attached client lands on the tab of the routing
   target client (fact 1). With zero clients, pre-focusing (`go-to-tab-*`) is a no-op;
   the client lands where the last client left off.
3. **No client targeting exists.** Neither 0.45.0 nor 0.45.1 adds a client-targeting
   flag to `action`, or a tab selector to `attach`.
4. **Stable tab IDs.** `action list-tabs -j` returns JSON objects including `tab_id`
   (int), `position`, `name`, `active`, and `other_focused_clients` (list of client IDs
   viewing the tab). `action new-tab` prints the new tab's ID on stdout.
   `go-to-tab-by-id`, `rename-tab-by-id`, `close-tab-by-id` exist.
5. **Rename works headless.** `rename-tab-by-id <id> <name>` renames with zero clients.
   Nonexistent IDs exit 0 silently (also true of `go-to-tab-by-id`).
6. **`new-tab --no-focus`** creates a tab without moving any client's focus.
7. **0×0 tab bug (upstream zellij-org/zellij#5594, open; fix PR #5612 unmerged).** A tab
   created while **no client is attached** is sized 0×0, logs `Failed to apply layout:
   Not enough room for panes`, is **silently discarded when the first client attaches**,
   and retains its pane's output without bound (reported server OOM at 36 GB). Tabs
   created while any client is attached are sized correctly and survive later
   detach/attach.
8. **`list-clients`** prints a whitespace table `CLIENT_ID ZELLIJ_PANE_ID RUNNING_COMMAND`
   (header line, then one row per client); no JSON flag.
9. **Missing session error.** When other sessions exist but `S` doesn't, actions print
   `Session 'S' not found. The following sessions are active: …` to stderr and **exit
   0** with empty stdout (rechecked 2026-10-24 for `list-tabs -j`, `list-clients`,
   `query-tab-names`). `ZellijCmd` therefore treats that stderr as a failure.
10. **`zellij attach S options --on-force-close detach`** sets this client's
    force-close behavior, so killing/hanging-up a client scry owns detaches rather than
    quitting the session regardless of user config.

## Design

### Pseudo-terminal client (`src/scry/backends/pty_client.py`)

scry runs zellij clients on a pty it owns instead of handing over its own tty. One
module provides:

- `PtyClient` — spawns an argv on a new pty of a given size; can write bytes to it,
  read from it, resize it, and terminate it.
- `proxy(client, gate, hold_timeout)` — puts scry's stdin in raw mode and copies bytes
  stdin→pty and pty→stdout, propagating SIGWINCH as a pty resize, until the child
  exits. Until `gate` is set (or `hold_timeout` elapses), **both directions are
  buffered**; on release, buffered output is flushed in one write, then buffered input
  is forwarded. This hides the wrong-tab first frame. Terminal state is restored on
  every exit path.

### Attach (`attach_window`)

1. Snapshot client IDs and each client's tab (`list-clients` + `list-tabs -j`).
2. Spawn `zellij attach S options --on-force-close detach` in a `PtyClient` sized to the
   real terminal; start `proxy` with a gate.
3. In a background thread: poll `list-clients` until a new client ID appears; write the
   configured **wake keys** to the new client's pty (one key per write, 50 ms apart);
   send `go-to-tab-by-id <tab>`; poll `list-tabs -j` until the new client ID is in the
   target tab's `other_focused_clients`. If another client moved instead (a user typed
   elsewhere in the window), send `go-to-tab-by-id <its previous tab>` to put it back
   (it is now the routing target), then re-wake and retry — at most 3 attempts.
   Set the gate whatever the outcome.
4. `attach_window` returns when the client exits (user detaches).

**Wake keys** are a scry config key, `zellij_wake_keys` (string; each character is
written as a separate keypress). Default `"\x07\x07"` (Ctrl-g Ctrl-g: lock/unlock under
zellij's default keybindings). David's config needs `"\x01\x1b"` (prefix Ctrl-a, then
Esc). The sequence must be one zellij consumes as a net no-op under the user's
keybindings; a wrong value types into the pane.

### Create (`create_detached_window`)

Same guards as today. If the session has **zero clients**, run a headless helper
client (`PtyClient`, 80×24, `options --on-force-close detach`, output drained and
discarded) for the duration of the creation, waiting until it appears in
`list-clients` first. Create with `new-tab --no-focus --name <name>`. This avoids the
0×0 bug and moves no real client.

### Other changes

- `list_windows` uses `list-tabs -j`: `window_id = str(tab_id)`, `window_name = name`,
  `window_active_clients = str(len(other_focused_clients))`.
- `rename_window` uses `rename-tab-by-id`, then verifies via `list-tabs -j` and raises
  `RuntimeError` if the tab is missing or the name didn't change.
- `Session '…' not found` is treated like "no session" in `list_windows`.
- The pre-focus + refocus thread is removed.
- Process-command error text "Invalid tmux window name" becomes "Invalid window name".
- Docs (README, CLAUDE.md, the 2026-07-06 design doc) are corrected; the zellij version
  floor becomes 0.45.1.

## Out of scope

- zellij plugins; auto-detecting wake keys from zellij's config.
- `session_attached` in zellij `list_sessions` stays `"0"` (unused by scry.py).
