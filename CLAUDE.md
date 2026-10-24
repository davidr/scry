# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Scry is an interactive terminal-multiplexer window manager with session grouping support. It provides a TUI for listing, creating, and switching between windows within a session group, with history-based navigation and multi-column display. The multiplexer backend is configurable — tmux (default) or zellij.

## Build & Development

```bash
uv sync                      # install project + dev dependencies
source .venv/bin/activate
scry                         # run the CLI
uv run pytest -v             # run the test suite
make install                 # uv tool install . — install scry as a uv tool on PATH
make uninstall               # uv tool uninstall scry
```

Distribution is via `uv tool install` (wrapped by the `Makefile`); the target machine needs only `uv`, which bootstraps its own Python and the locked dependencies. There is no shiv/zipapp build anymore.

## Code Style

- **Ruff** for formatting, linting, and import sorting: `uv run ruff format .` and `uv run ruff check . --fix`

## Architecture

Three-layer design:

1. **Entry point** (`src/scry/__main__.py`) — minimal: defines `run_scry()`, which calls `do_table_loop()` from the core module. The pyproject.toml console script points at `scry.__main__:run_scry`, and the module-level call is guarded by `if __name__ == "__main__":` so importing the module is side-effect free.

2. **Core UI/logic** (`src/scry/scry.py`) — the main interactive loop and all display logic:
   - `do_table_loop()` — main REPL: list windows, prompt for command, dispatch, attach
   - `process_command()` — command dispatch, returns `(window_to_attach, error_message)` tuple
   - `draw_table_windows()` / `format_window_strings()` — Rich-based multi-column display with history highlighting (magenta=most recent, green=2nd, blue=3rd). Optionally collapses shared `prefix+NN` prefixes when `config["hide_prefixes"]` is set (off by default; `--hide-prefixes` flag / `hide_prefixes` config key)
   - `parse_args_and_configure()` — three-tier config: defaults → `~/.scry.yml` → CLI args. Called at **module load time** and stored in module-level `config` dict
   - `WINDOW_HISTORY` — module-level deque tracking recently attached windows

3. **Multiplexer backends** (`src/scry/backends/`) — pluggable backend package:
   - `base.py` — `MuxBackend`, the abstract base class defining the backend interface (list/create/attach/rename windows, session groups, etc.)
   - `tmux.py` — `TmuxBackend`, the default backend; wraps `TmuxCmd`/`TmuxFmtCmd`, subprocess wrappers for `tmux` (`TmuxFmtCmd` adds `-F` format string support, parsing output via the `__X__` separator into a list of dicts)
   - `zellij.py` — `ZellijBackend`, wraps `ZellijCmd` around the `zellij` CLI
   - `pty_client.py` — `PtyClient` runs zellij clients on a pty scry owns (exec trampoline makes it the controlling tty); `proxy` relays the user's terminal, holding I/O until the attaching client is on the right tab (2 s fallback) and restoring the terminal on every exit path
   - `__init__.py` — `get_backend()` factory: instantiates the backend named by `config["backend"]` (`"tmux"` or `"zellij"`); binary lookup happens in each backend's constructor, not at import time

`src/scry/bin_utils.py` has a single utility `find_bin_in_path()` to locate a multiplexer binary.

## Key Design Decisions

- **Backend abstraction**: `--backend`/`-b` flag or `backend` config key selects `tmux` (default) or `zellij` via `get_backend()`. An invalid backend name or a missing binary for the selected backend raises a clear error rather than a traceback. Zellij (requires >= 0.45.1) matches tmux behavior: window IDs are stable tab IDs (`list-tabs -j`), `#` comes from each tab's `other_focused_clients`, `Session '…' not found` is treated as no session, and `rename_window` uses `rename-tab-by-id` with post-verification. Attach snapshots clients/tabs, raises "window <id> no longer exists" if the tab is gone (shown at scry's prompt by `do_table_loop`), spawns `zellij attach S options --on-force-close detach` on a `PtyClient` pty, and a director thread writes `zellij_wake_keys` (default `"\x07\x07"`; each char one keypress; must be a net no-op under the user's keybindings or it types into the pane) so zellij routes the next remote action to the new client, sends `go-to-tab-by-id`, confirms, and puts back any other client pulled onto the target (≤3 attempts). Creation uses `new-tab --no-focus`; because of zellij bug #5594 (tabs created with zero clients attached are 0x0 and discarded on next attach) a headless helper client runs during creation when none is attached, new tabs are size-verified and recreated under a forced helper if needed, and `l` shares one helper for the batch. Remaining caveats: reliance on undocumented "most recently active client" routing, and zellij 0.45.1 client-ID reuse can rarely leave the attaching client undirected. Live tests: `SCRY_LIVE_ZELLIJ=1 uv run pytest -m live`.
- **Session groups**: Under the tmux backend, uses tmux's session group feature — the "main" session group is the default, and numbered 8-digit sessions are auto-created for attachment. This is tmux-specific: under the zellij backend, a session group maps to a single zellij session shared by all attached clients.
- **Config is global**: `parse_args_and_configure()` runs at import time of `scry.py`; the resulting `config` dict is used as module-level state throughout.
- **Testing**: 189 tests via pytest (plus opt-in live zellij tests). Import-time side effects (`sys.argv` parsing, tmux binary lookup) are patched in `tests/conftest.py` before any scry module is imported. Run with `uv run pytest -v`.
