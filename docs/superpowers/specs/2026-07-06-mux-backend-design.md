# Configurable Multiplexer Backend (tmux / zellij) — Design

**Date:** 2026-07-06
**Status:** Approved pending spec review
**Branch:** `mux-backend` (worktree `~/worktrees/scry/mux-backend`)

## Goal

Make scry's terminal-multiplexer backend configurable: tmux remains the default; zellij
(tested against 0.44.3, found on `PATH`) becomes a first-class alternative. Using scry
should feel the same under either backend, with a small number of documented differences.

## Requirements

- Backend selected via config with the existing three-tier precedence:
  defaults → `~/.scry.yml` → CLI args. New key `backend` (`tmux` | `zellij`, default
  `tmux`), new CLI flag `--backend` / `-b`.
- Multi-client use is a core workflow: several terminals attached to different windows
  of the same window set at the same time must work under both backends.
- The UI (columns, history colors, commands, prompt) is identical under both backends.
- Accepted zellij degradations (documented, not silent surprises):
  - No `#` attached-client indicator (zellij has no per-tab client query).
  - Window identity is the tab name, not a stable ID: renaming a window resets its
    position in the history highlighting.
  - Window rename may not be supportable remotely (see Risks); if the chosen sequence
    proves unreliable, `r` returns a clear "rename not supported under zellij" error.

## Architecture

New `src/scry/backends/` package; `src/scry/tmuxcmd.py` is deleted and its code moves,
essentially unchanged, into the tmux backend.

```
src/scry/backends/__init__.py   # get_backend(config) -> MuxBackend factory
src/scry/backends/base.py       # MuxBackend abstract base class
src/scry/backends/tmux.py       # TmuxBackend + TmuxCmd/TmuxFmtCmd (from tmuxcmd.py)
src/scry/backends/zellij.py     # ZellijBackend + ZellijCmd subprocess wrapper
```

### MuxBackend interface

Exactly the operations `scry.py` uses today, with the dict shapes it already consumes:

| Method | Returns | Notes |
|---|---|---|
| `list_windows(session_group)` | `List[Dict]` with `window_id`, `window_name`, `window_active_clients` | sorted by `window_name`; `[]` if no server/session |
| `list_sessions()` | `List[Dict]` with `session_id`, `session_name`, `session_attached`, `session_group` | `[]` if no server |
| `create_detached_window(name, session_group)` | `None` | raises `RuntimeError` if group missing or name taken |
| `rename_window(window_id, new_name, session_group)` | `None` | |
| `attach_window(window_id, session_group)` | `None` | blocks until the client detaches |
| `window_exists(name, session_group)` | `bool` | |
| `session_exists(name)` | `bool` | |
| `ensure_session_group(session_group)` | `None` | creates the group/session detached if missing |

- Backends raise `RuntimeError` carrying the CLI's stderr (current contract, unchanged).
- Binary lookup (`find_bin_in_path`) happens in the backend constructor, **not** at
  import time. Only the selected backend's binary is resolved; a missing binary or an
  invalid `backend` value exits at startup with a clear message. This also removes an
  import-time side effect `tests/conftest.py` currently patches around.
- `scry.py` creates one module-level `backend = get_backend(config)` after config load
  (consistent with the module-level `config` pattern) and calls methods on it. Display,
  history, and command-dispatch logic do not change.
- The 8-digit throwaway-session mechanism is a private detail of
  `TmuxBackend.attach_window`, no longer visible to `scry.py`.

## Zellij backend semantics

The `session_group` config value maps to a single zellij session name (default `main`);
zellij tabs are scry windows. All scry clients attach to that one session, relying on
zellij's multiplayer support (default `mirror_session false`) for independent per-client
tab focus — no throwaway sessions needed.

| Interface method | Zellij implementation |
|---|---|
| `ensure_session_group` | `zellij attach --create-background <group>` |
| `list_windows` | `zellij --session <group> action query-tab-names`; tab name is both `window_id` and `window_name`; `window_active_clients` always `"0"` |
| `create_detached_window` | `zellij --session <group> action new-tab -n <name>`, after the same group-exists / name-taken guards as tmux (zellij allows duplicate tab names; scry forbids them) |
| `attach_window` | `zellij --session <group> action go-to-tab-name <name>`, then blocking `zellij attach <group>` |
| `rename_window` | `go-to-tab-name <old>` then `rename-tab <new>` (no remote rename-by-name exists; tab IDs cannot be enumerated in 0.44). Fallback if unreliable: clear unsupported-operation error |
| `list_sessions` / `session_exists` | `zellij list-sessions -n`; sessions marked EXITED are treated as missing (attach resurrects them); `session_id` and `session_group` both mirror `session_name`, `session_attached` parsed from the listing |

Window-name validation (`^[\w+-.]+$`) stays shared and unchanged for both backends.

## Risks and the spike

Zellij remote actions (`go-to-tab-name`, `new-tab`, `rename-tab`) target the **session**,
not a specific client. With multiple attached clients, they may move another client's
focus. This cannot be verified headlessly, so implementation **starts with a manual
spike**: two real clients on one throwaway session, observing what each remote action
does to the other client's focus.

- Outcome (a): focus is per-client → design works as specified.
- Outcome (b): remote actions move all clients → attach still works; adjust (e.g. attach
  first and land on the session's last-focused tab, or document the focus yank as a
  zellij limitation) and record the decision in this doc before the main build.

The rename sequence's behavior with zero attached clients is also verified in the spike.

## Testing

- Existing tests (92) keep passing; imports and patches move from `scry.tmuxcmd` to
  `scry.backends.*`. `conftest.py` gets simpler because binary lookup is no longer an
  import-time side effect.
- New per-backend unit tests with mocked `subprocess.run`: each interface method issues
  the expected argv and parses captured real tmux / zellij 0.44.3 output fixtures.
- A shared contract-test module parameterized over both backends asserts that the same
  interface call yields the same normalized dict shapes — "seamless" is enforced by CI.
- Multi-client focus behavior is covered by the manual spike checklist, not unit tests.
- **Any test or spike touching a live tmux/zellij server MUST use a throwaway session
  group (e.g. `scry-test-<pid>`), never the real `main` group, and must kill its
  sessions afterwards.** David's daily environment lives in `main`.

## Documentation

README and CLAUDE.md are updated with: the `backend` config key and `--backend`/`-b`
flag, the zellij version requirement (tested on 0.44.3), and the documented
degradations under zellij (no `#` indicator, rename caveat, name-as-identity).

## Spike results (2026-07-06)

**Part A (headless, zero attached clients):**

- Rename with zero clients: **FAILS SILENTLY** — `go-to-tab-name tab-one` and
  `rename-tab tab-renamed` both exit 0, but `query-tab-names` still shows
  `tab-one`; no tab was renamed. With no attached client there is no focused
  tab, so `rename-tab` is a no-op that reports success.
- Implication for `go-to-tab-name`: it also appears to be a client-focus
  operation; with zero clients it likely no-ops too (cannot confirm headlessly
  — Part B question 2/5 covers it).

**Decision (Part A):** `rename_window` under zellij raises
`RuntimeError("window rename is not supported with the zellij backend")` —
a silent no-op masquerading as success is worse than a clear error.

**Part B (two-client focus test, user-run 2026-07-11) plus controller headless
verification (zellij clients hosted in throwaway tmux sessions):**

- Independent per-client tab focus: **yes** — two clients held different tabs
  simultaneously (zellij multiplayer works as assumed).
- Remote `go-to-tab-name` / `new-tab` with clients attached: moves the
  session's **first attached client only** (user observed T1 yanked; T2
  unaffected).
- Remote `go-to-tab-name` with **zero** clients: **no-op** — verified via
  `dump-layout`: focus stayed on the last-created tab despite pre-focusing
  another; the next client to attach landed on the last-focused tab, not the
  pre-focused one.
- A newly attaching client lands on the session's currently-focused tab, so
  pre-focus **does** direct a new client when at least one other client is
  already attached.

**Decision (final):**

- `attach_window` = `go-to-tab-name <tab>` (directs the new client when other
  clients exist; harmless no-op otherwise), then a short-lived daemon thread
  that re-fires `go-to-tab-name` at ~0.4s and ~1.2s (directs our client in the
  zero-client case once it has connected; idempotent otherwise; failures
  ignored), then blocking `zellij attach <session>`.
- `rename_window` = `RuntimeError("window rename is not supported with the
  zellij backend")` (per Part A: the focus-then-rename sequence reports
  success without renaming anything).
- Documented limitation: with multiple clients attached, attaching to a
  window (and creating one — `new-tab` has the same routing) also pulls the
  session's first client to that tab. Zellij 0.44 remote actions cannot
  target a specific client.

## Out of scope

- Per-tab attached-client detection under zellij (revisit if zellij grows the API).
- Zellij layouts, plugins, or any zellij-specific features beyond parity.
- Auto-detection of the backend from the environment (`ZELLIJ` env var).
