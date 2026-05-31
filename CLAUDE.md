# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Scry is an interactive tmux window manager with session grouping support. It provides a TUI for listing, creating, and switching between tmux windows within a session group, with history-based navigation and multi-column display.

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

3. **tmux abstraction** (`src/scry/tmuxcmd.py`) — subprocess wrapper for tmux:
   - `TmuxCmd` — base class: runs `tmux <args>` via `subprocess.run()`, raises `RuntimeError` on non-zero exit
   - `TmuxFmtCmd(TmuxCmd)` — extends with `-F` format string support; parses output using `__X__` separator into list of dicts
   - Module-level functions (`tmux_list_windows`, `tmux_list_sessions`, `tmux_attach_window`, etc.) compose these classes

`src/scry/bin_utils.py` has a single utility `find_bin_in_path()` to locate the tmux binary.

## Key Design Decisions

- **Session groups**: Uses tmux's session group feature. The "main" session group is the default. Numbered 8-digit sessions are auto-created for attachment.
- **Config is global**: `parse_args_and_configure()` runs at import time of `scry.py`; the resulting `config` dict is used as module-level state throughout.
- **Testing**: 92 tests via pytest. Import-time side effects (`sys.argv` parsing, tmux binary lookup) are patched in `tests/conftest.py` before any scry module is imported. Run with `uv run pytest -v`.
