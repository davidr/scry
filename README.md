# scry

> **Warning**: Honestly, you really shouldn't use this for anything. It's awful and I've been using
it forever and I'm too old to stop. I'm only making it public so that I can clone it from remote sites.

An interactive terminal-multiplexer window manager (tmux by default, zellij optional) with session grouping support, providing a user-friendly interface for managing windows and sessions.

## Features

- Interactive window listing with multi-column display
- Easy window creation and attachment
- Session group management
- History-based window switching
- Visual indicators for active and recently used windows
- Configurable display parameters
- Window list dump and load functionality

## Installation

### Prerequisites

- Python 3.12+
- tmux (default backend) and/or zellij >= 0.45.1
- [uv](https://github.com/astral-sh/uv) (Python package installer and resolver)

### Recommended Installation (Virtual Environment using uv)

1.  **Install `scry` and its dependencies:**
    ```bash
    uv sync --no-dev
    source .venv/bin/activate  # On Linux/macOS
    ```
    This will create a virtual environment, install `scry` and its dependencies from the lock file. The `scry` command will then be available.

### Install as a command (on your PATH)

To get a `scry` executable on your PATH from a fresh checkout, run:

```bash
make install        # equivalent to: uv tool install .
```

This builds `scry` and installs it as a [uv tool](https://docs.astral.sh/uv/guides/tools/), placing the `scry` executable in uv's tool-bin directory (`~/.local/bin` by default). The only requirement on the machine is `uv` — it bootstraps its own Python interpreter and the locked dependencies into an isolated environment.

```bash
make reinstall      # uv tool install . --reinstall (after pulling changes)
make uninstall      # uv tool uninstall scry
```

If `scry` isn't found after install, ensure uv's tool-bin directory is on your PATH (`uv tool update-shell` sets this up).

## Configuration

Scry can be configured through:

1. Default settings
2. Configuration file (`~/.scry.yml`)
3. Command-line arguments

### Command-line Options

- `-m, --minnamelen`: Minimum length for displayed window names
- `-c, --columns`: Number of columns to display windows in
- `-s, --session_group`: Session group to manage
- `-d, --debug`: Enable debug logging
- `-l, --log-file`: Path to the log file
- `--dump-file`: Path to the window dump file
- `--hide-prefixes`: Collapse shared `prefix+NN` prefixes in the display (off by default)
- `--backend`/`-b`: multiplexer backend, `tmux` (default) or `zellij`

### Configuration File

Create `~/.scry.yml` with any of these settings:

```yaml
minnamelen: 15
n_cols: 4
fmt_overhead: 3
session_group: "main"
debug: false
log_file: "/tmp/scry.log"
dump_file: "~/.scry_windows.yml"
hide_prefixes: false
backend: tmux    # multiplexer backend: "tmux" (default) or "zellij"
zellij_wake_keys: "\x07\x07"   # zellij only; see "Zellij backend" below
zellij_bin: ~/bin/zellij       # zellij only, optional; see below
```

`zellij_bin` is optional: an explicit path to the zellij binary (`~` is expanded), used
instead of searching `PATH`. It is for when zellij isn't on `PATH`, e.g. under
non-interactive ssh. scry exits with an error if it isn't an executable file.

When `hide_prefixes` is enabled, a window named like `proj+02` that sits directly
below another `proj+NN` window in the same column has its shared prefix blanked
out, so only the `+NN` portion shows. It is disabled by default.

## Usage

### Basic Commands

- `##`: Select window by numerical index
- `n <name>`: Create new window with specified name
- `r ## <name>`: Rename window
- `s`: Swap to second most recent window
- `u`: Update screen
- `d`: Dump list of active windows
- `l`: Load windows from dump file
- `q`: Quit
- `?`: Show help

### Window Navigation

- Windows are displayed in a multi-column layout
- Recently used windows are highlighted
- Active windows are marked with a `#`
- Empty command returns to the most recent window

### Zellij backend

Pass `--backend zellij` (or `-b zellij`), or set `backend: zellij` in `~/.scry.yml`, to run
scry against [zellij](https://zellij.dev/) instead of tmux. Requires zellij >= 0.45.1 (for
`new-tab --no-focus`); developed against 0.45.1. An invalid backend value, or a missing
binary for the selected backend, exits with a clear error rather than a traceback.

Zellij windows map to zellij tabs (identified by zellij's stable tab IDs), and — because
zellij sessions are multiplayer — all clients share a single zellij session named after the
session group, rather than each client getting its own tmux session in the group. Behavior
matches tmux: the `#` indicator shows tabs that other clients are focused on, and `r` renames
windows (verified after the fact).

- **Attaching.** scry runs `zellij attach` on a pty it owns (`backends/pty_client.py`) and
  relays your terminal to it, holding input and output until the new client is on the right
  tab (2 s fallback), and restoring the terminal on every exit path. A director thread sends
  `zellij_wake_keys` to the new client so zellij routes the next remote action to it, then
  issues `go-to-tab-by-id`, confirms, and puts back any other client that got pulled onto the
  target tab (up to 3 attempts). Typically about 250 ms from spawn to reveal. If the tab was
  closed meanwhile, scry reports "window <id> no longer exists" at its prompt.
- **Creating windows.** `new-tab --no-focus` moves no client. Because of zellij bug #5594
  (tabs created while no client is attached are 0x0 and silently discarded on the next
  attach), scry runs a short-lived headless helper client during creation when no clients
  are attached, checks that each new tab has a real size, and recreates it under a forced
  helper if not. `l` uses one helper for the whole batch.
- **`zellij_wake_keys`** (default `"\x07\x07"`, Ctrl-g twice) — each character is one
  keypress sent to the newly attached client. It must be a sequence zellij consumes as a net
  no-op under *your* keybindings; the default is right for zellij's stock keybindings (lock
  then unlock). For a tmux-style config with prefix Ctrl-a, use `"\x01\x1b"` (Ctrl-a, then
  Esc) or `"\x01."` (Ctrl-a, then `.`, if `.` returns to normal mode). In YAML use a
  double-quoted string so `\x` escapes work. **Warning:** any key zellij doesn't consume is
  typed into the pane the new client first lands on, which can be a pane someone is using
  in another terminal. With the default under a config that leaves Ctrl-g unbound, zsh's
  emacs mode discards the line they are typing.

Remaining caveats: directing the attaching client relies on zellij's undocumented "most
recently active client" routing; zellij 0.45.1 reuses client IDs, so in rare races the
attaching client may not be directed (it then shows whichever tab zellij picks).

Live tests against a real zellij (throwaway `scry-test-*` sessions, stock keybindings):
`SCRY_LIVE_ZELLIJ=1 uv run pytest -m live`.

## Development

1.  **Clone the repository:**
    ```bash
    git clone <repository-url>
    cd scry
    ```

2.  **Set up the development environment:**
    ```bash
    uv sync
    source .venv/bin/activate
    ```

### Running Tests

```bash
uv run pytest -v
```

### Code Style

The project uses Ruff for formatting, linting, and import sorting:
- `uv run ruff format .`
- `uv run ruff check . --fix`

### Project Structure

- `src/scry/`
  - `__main__.py`: Entry point
  - `scry.py`: Core functionality
  - `backends/`: multiplexer backends (`base.py` interface, `tmux.py`, `zellij.py`, `pty_client.py`)
  - `bin_utils.py`: Binary path utilities
- `tests/`: Test suite (189 tests)
- `pyproject.toml`: Project metadata and dependencies for `uv`.
- `README.md`: This file.

## License

BSD 3-Clause License

## Author

David Ressman (davidr@ressman.org)
