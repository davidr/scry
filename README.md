# scry

> **Warning**: Honestly, you really shouldn't use this for anything. It's awful and I've been using
it forever and I'm too old to stop. I'm only making it public so that I can clone it from remote sites.

An interactive terminal-multiplexer window manager (tmux by default, zellij optional) with session grouping support, providing a user-friendly interface for managing tmux windows and sessions.

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
- tmux (default backend) and/or zellij >= 0.44
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
```

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
scry against [zellij](https://zellij.dev/) instead of tmux (tested on 0.44.3). An invalid
backend value, or a missing binary for the selected backend, exits with a clear error rather
than a traceback.

Zellij windows map to zellij tabs, and — because zellij sessions are multiplayer — all
clients share a single zellij session named after the session group, rather than each client
getting its own tmux session in the group. This changes a few things from the tmux behavior:

- No `#` attached-client indicator: zellij has no way to query which client is on which tab,
  so the display can't show who's currently attached to a window.
- **Window rename (`r`) is not supported under zellij.** Zellij's remote rename silently
  reports success without renaming anything when there's no attached client to focus first,
  so scry raises a clear error instead of pretending it worked. (Window identity is the tab
  name, and renaming would otherwise reset a window's history-highlight position — moot
  since rename isn't available.)
- Attaching to a window pre-focuses its tab, then re-fires the focus shortly after connecting
  (zellij can't direct a client before it has connected). **Limitation:** with more than one
  client attached, attaching to (or creating) a window also pulls the session's *first*
  attached client to that tab — zellij 0.44's remote actions can't target a specific client.

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
  - `backends/`: multiplexer backends (`base.py` interface, `tmux.py`, `zellij.py`)
  - `bin_utils.py`: Binary path utilities
- `tests/`: Test suite (138 tests)
- `pyproject.toml`: Project metadata and dependencies for `uv`.
- `README.md`: This file.

## License

BSD 3-Clause License

## Author

David Ressman (davidr@ressman.org)
