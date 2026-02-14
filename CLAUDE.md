# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Scry is an interactive tmux window manager with session grouping support. It provides a TUI for listing, creating, and switching between tmux windows within a session group, with history-based navigation and multi-column display.

## Build & Development

```bash
uv venv && source .venv/bin/activate
uv pip install -e .          # editable install for development
scry                         # run the CLI
```

## Code Style

- **Black** (line length 120): `uv run black .`
- **Ruff** for linting and import sorting: `uv run ruff check . --fix` and `uv run ruff format .`
- **isort** configured for Black compatibility

## Architecture

Three-layer design:

1. **Entry point** (`scry/__main__.py`) — minimal, calls `do_table_loop()` from the core module. Note: `run_scry()` is both defined and called at module level (the entry point in pyproject.toml also points to `run_scry`).

2. **Core UI/logic** (`scry/scry.py`) — the main interactive loop and all display logic:
   - `do_table_loop()` — main REPL: list windows, prompt for command, dispatch, attach
   - `process_command()` — command dispatch, returns `(window_to_attach, error_message)` tuple
   - `draw_table_windows()` / `format_window_strings()` — Rich-based multi-column display with history highlighting (magenta=most recent, green=2nd, blue=3rd)
   - `parse_args_and_configure()` — three-tier config: defaults → `~/.scry.yml` → CLI args. Called at **module load time** and stored in module-level `config` dict
   - `WINDOW_HISTORY` — module-level deque tracking recently attached windows

3. **tmux abstraction** (`scry/tmuxcmd.py`) — subprocess wrapper for tmux:
   - `TmuxCmd` — base class: runs `tmux <args>` via `subprocess.run()`, raises `RuntimeError` on non-zero exit
   - `TmuxFmtCmd(TmuxCmd)` — extends with `-F` format string support; parses output using `__X__` separator into list of dicts
   - Module-level functions (`tmux_list_windows`, `tmux_list_sessions`, `tmux_attach_window`, etc.) compose these classes

`scry/bin_utils.py` has a single utility `find_bin_in_path()` to locate the tmux binary.

## Key Design Decisions

- **Session groups**: Uses tmux's session group feature. The "main" session group is the default. Numbered 8-digit sessions are auto-created for attachment.
- **Config is global**: `parse_args_and_configure()` runs at import time of `scry.py`; the resulting `config` dict is used as module-level state throughout.
- **No tests currently exist** — pytest infrastructure is set up but no test files are present.
