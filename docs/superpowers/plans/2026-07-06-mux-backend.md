# Configurable Mux Backend (tmux / zellij) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make scry's terminal multiplexer backend configurable between tmux (default) and zellij, behind a common `MuxBackend` interface, with identical UI behavior under both.

**Architecture:** A new `src/scry/backends/` package holds a `MuxBackend` ABC, a `TmuxBackend` (the existing `tmuxcmd.py` code reshaped into a class), a `ZellijBackend`, and a `get_backend(config)` factory. `scry.py` creates one module-level `backend` object after config load and calls methods on it; display/history/command logic is untouched. Spec: `docs/superpowers/specs/2026-07-06-mux-backend-design.md`.

**Tech Stack:** Python 3, stdlib `subprocess`/`abc`, pytest + unittest.mock, uv, ruff.

## Global Constraints

- Work happens in worktree `~/worktrees/scry/mux-backend`, branch `mux-backend`. Run all commands from that directory.
- New config key `backend`, default `"tmux"`, allowed values `tmux` | `zellij`. CLI flag `--backend` / `-b`. Precedence: defaults → `~/.scry.yml` → CLI args.
- All backend failures raise `RuntimeError` carrying the CLI's stderr. `list_windows`/`list_sessions` return `[]` when the server/session doesn't exist yet.
- Window dicts always have exactly the keys `window_id`, `window_name`, `window_active_clients`; session dicts `session_id`, `session_name`, `session_attached`, `session_group`.
- Zellij version floor: tested against zellij 0.44.3.
- **Any command touching a live tmux or zellij server MUST use a throwaway session name (`scry-test-$$` style), NEVER the user's real `main` session group, and must kill/delete its sessions afterwards.** The user's daily environment lives in `main`.
- Run `uv run ruff format .` and `uv run ruff check . --fix` before every commit.
- Run tests with `uv run pytest -q` (full suite must be green at every commit).

---

### Task 1: Zellij behavior spike (partially manual — needs the user)

Resolves the spec's open risk: do zellij remote actions (`go-to-tab-name`, `new-tab`, `rename-tab`) move the focus of *other* attached clients? Also verifies the rename sequence works with zero attached clients.

Part A is scripted and runs headlessly. Part B needs the user at two terminals — **if the user isn't available now, do Part A, then continue with Tasks 2–5 (they don't depend on the spike) and finish Part B before starting Task 6.**

**Files:**
- Modify: `docs/superpowers/specs/2026-07-06-mux-backend-design.md` (append a "Spike results" section)

**Interfaces:**
- Consumes: nothing (raw zellij CLI).
- Produces: recorded spike outcomes that Task 6 reads to pick the `attach_window`/`rename_window` implementation variant.

- [ ] **Step 1: Run Part A — rename with zero attached clients (headless)**

```bash
Z=~/bin/zellij
S=scry-test-$$
$Z attach --create-background $S
$Z --session $S action new-tab --name tab-one
$Z --session $S action new-tab --name tab-two
$Z --session $S action go-to-tab-name tab-one && \
  $Z --session $S action rename-tab tab-renamed && echo RENAME-SEQUENCE-OK
$Z --session $S action query-tab-names
$Z kill-session $S; $Z delete-session $S --force
```

Record: did `RENAME-SEQUENCE-OK` print, and does `query-tab-names` show `tab-renamed` (and not `tab-one`)? If the sequence errors (e.g. "no active client"), record that too — Task 6 then uses the unsupported-rename fallback.

- [ ] **Step 2: Ask the user to run Part B — two-client focus test**

Give the user these instructions verbatim (they need two real terminals; a third shell runs the remote actions):

```bash
# One-time setup (any shell):
Z=~/bin/zellij; S=scry-test-mc
$Z attach --create-background $S
$Z --session $S action new-tab --name tab-one
$Z --session $S action new-tab --name tab-two

# Terminal 1:  ~/bin/zellij attach scry-test-mc   → move to tab-one
# Terminal 2:  ~/bin/zellij attach scry-test-mc   → move to tab-two
```

Then, from the third shell, run each remote action and note what happens in Terminals 1 and 2:

| # | Action (third shell) | Question | Result |
|---|---|---|---|
| 1 | (none — just attach both) | Can T1 and T2 sit on different tabs at once? | |
| 2 | `$Z --session $S action go-to-tab-name tab-one` | Did T1/T2 focus change? | |
| 3 | `$Z --session $S action new-tab --name tab-three` | Did T1/T2 jump to the new tab? | |
| 4 | `$Z --session $S action go-to-tab-name tab-two && $Z --session $S action rename-tab tab-2b` | Which tab got renamed? Did T1/T2 focus move? | |

Cleanup: `$Z kill-session $S; $Z delete-session $S --force`

- [ ] **Step 3: Record results in the spec**

Append to `docs/superpowers/specs/2026-07-06-mux-backend-design.md`:

```markdown
## Spike results (2026-MM-DD)

- Rename with zero clients: <works / fails with "...">
- Independent per-client tab focus: <yes / no>
- `go-to-tab-name` with clients attached: <per-client / moves all clients>
- `new-tab` with clients attached: <no focus change / moves all clients>
- Rename sequence with clients attached: <renamed correct tab? focus effects?>

**Decision:** attach_window uses <variant A: go-to-tab-name then attach / variant B: ...>;
rename_window is <supported via focus-then-rename / unsupported error>.
```

If the results show remote actions yank all clients' focus (outcome (b) in the spec), ask the user whether to keep the pre-focus behavior (attach always lands on the right window, other clients get yanked — documented limitation) or drop it (no yank, but attach lands on the session's current tab). Record their choice as the Decision.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-07-06-mux-backend-design.md
git commit -m "spike: record zellij multi-client focus and rename behavior"
```

---

### Task 2: MuxBackend abstract base class

**Files:**
- Create: `src/scry/backends/__init__.py` (empty for now; factory arrives in Task 4)
- Create: `src/scry/backends/base.py`
- Test: `tests/test_backend_base.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `MuxBackend` ABC with abstract methods `list_windows(session_group) -> List[Dict[str,str]]`, `list_sessions() -> List[Dict[str,str]]`, `create_detached_window(window_name, session_group) -> None`, `rename_window(window_id, new_name, session_group) -> None`, `attach_window(window_id, session_group) -> None`, `ensure_session_group(session_group) -> None`; concrete helpers `window_exists(window_name, session_group) -> bool` and `session_exists(session_name) -> bool` implemented on the ABC in terms of the list methods.

- [ ] **Step 1: Write the failing test**

Create `tests/test_backend_base.py`:

```python
"""Tests for the MuxBackend abstract base class."""

from typing import Dict, List

import pytest

from scry.backends.base import MuxBackend


class FakeBackend(MuxBackend):
    """Minimal concrete backend for exercising the ABC's shared helpers."""

    def __init__(self, windows, sessions):
        self._windows = windows
        self._sessions = sessions

    def list_windows(self, session_group: str) -> List[Dict[str, str]]:
        return self._windows

    def list_sessions(self) -> List[Dict[str, str]]:
        return self._sessions

    def create_detached_window(self, window_name: str, session_group: str) -> None:
        pass

    def rename_window(self, window_id: str, new_name: str, session_group: str) -> None:
        pass

    def attach_window(self, window_id: str, session_group: str) -> None:
        pass

    def ensure_session_group(self, session_group: str) -> None:
        pass


def test_muxbackend_cannot_be_instantiated():
    with pytest.raises(TypeError):
        MuxBackend()


def test_window_exists_uses_list_windows():
    backend = FakeBackend(
        windows=[{"window_id": "@1", "window_name": "alpha", "window_active_clients": "0"}],
        sessions=[],
    )
    assert backend.window_exists("alpha", "main") is True
    assert backend.window_exists("missing", "main") is False


def test_session_exists_uses_list_sessions():
    backend = FakeBackend(
        windows=[],
        sessions=[{"session_id": "$0", "session_name": "main", "session_attached": "1", "session_group": "main"}],
    )
    assert backend.session_exists("main") is True
    assert backend.session_exists("missing") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_backend_base.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'scry.backends'`

- [ ] **Step 3: Write the implementation**

Create `src/scry/backends/__init__.py`:

```python
# -*- coding: utf-8 -*-
"""Multiplexer backend implementations."""
```

Create `src/scry/backends/base.py`:

```python
# -*- coding: utf-8 -*-
"""Abstract interface that every multiplexer backend implements."""

from abc import ABC, abstractmethod
from typing import Dict, List


class MuxBackend(ABC):
    """The operations scry needs from a terminal multiplexer.

    Listing methods return dicts with normalized keys (window_id, window_name,
    window_active_clients / session_id, session_name, session_attached,
    session_group) and return [] when the server or session does not exist
    yet. All other failures are raised as RuntimeError carrying the
    underlying CLI's stderr.
    """

    @abstractmethod
    def list_windows(self, session_group: str) -> List[Dict[str, str]]:
        """Return the group's windows, sorted by window_name."""

    @abstractmethod
    def list_sessions(self) -> List[Dict[str, str]]:
        """Return all sessions, sorted by session_name."""

    @abstractmethod
    def create_detached_window(self, window_name: str, session_group: str) -> None:
        """Create a window without attaching to it.

        Raises:
            RuntimeError: if the session group does not exist or a window
                with this name already exists.
        """

    @abstractmethod
    def rename_window(self, window_id: str, new_name: str, session_group: str) -> None:
        """Rename a window."""

    @abstractmethod
    def attach_window(self, window_id: str, session_group: str) -> None:
        """Attach this client to a window; blocks until the client detaches."""

    @abstractmethod
    def ensure_session_group(self, session_group: str) -> None:
        """Create the session group, detached, if it does not already exist."""

    def window_exists(self, window_name: str, session_group: str) -> bool:
        """Check whether a window with this name exists in the group."""
        return window_name in [w["window_name"] for w in self.list_windows(session_group)]

    def session_exists(self, session_name: str) -> bool:
        """Check whether a session with this name exists."""
        return session_name in [s["session_name"] for s in self.list_sessions()]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_backend_base.py -v`
Expected: 3 passed

- [ ] **Step 5: Full suite, lint, commit**

```bash
uv run pytest -q          # expect: all green (existing 92 + 3 new)
uv run ruff format . && uv run ruff check . --fix
git add src/scry/backends tests/test_backend_base.py
git commit -m "feat: add MuxBackend abstract base class"
```

---

### Task 3: TmuxBackend

Moves `src/scry/tmuxcmd.py` logic into `src/scry/backends/tmux.py` as a class. `tmuxcmd.py` stays in place (still imported by `scry.py`) until Task 5 deletes it. Two deliberate changes from the original: (1) `TmuxCmd`/`TmuxFmtCmd` take the tmux binary path as a constructor argument instead of reading a module-level global resolved at import time; (2) `list_windows`/`list_sessions` re-raise unrecognized `RuntimeError`s instead of falling through to an `UnboundLocalError` (latent bug in the original).

**Files:**
- Create: `src/scry/backends/tmux.py`
- Test: `tests/test_backend_tmux.py`

**Interfaces:**
- Consumes: `MuxBackend` from `scry.backends.base`; `find_bin_in_path` from `scry.bin_utils`.
- Produces: `TmuxBackend()` (no constructor args; resolves the tmux binary via `find_bin_in_path("tmux")` in `__init__`, which raises `ValueError` if missing). Also `TmuxCmd(tmux_bin: str, cmd_args: List[str])` and `TmuxFmtCmd(tmux_bin: str, args: List[str], fmt_keys: List[str])` with `.stdout` properties, used by Task 7's contract tests.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_backend_tmux.py` (port of `tests/test_tmuxcmd.py`; patch targets move from `scry.tmuxcmd.*` to `scry.backends.tmux.*`, module functions become methods, internal helpers are patched with `patch.object(backend, ...)`):

```python
"""Tests for the tmux backend."""

from unittest.mock import MagicMock, patch

import pytest

from scry.backends.tmux import TmuxBackend, TmuxCmd, TmuxFmtCmd


@pytest.fixture
def backend():
    with patch("scry.backends.tmux.find_bin_in_path", return_value="/usr/bin/tmux"):
        return TmuxBackend()


# --- TmuxCmd / TmuxFmtCmd class tests ---


@patch("scry.backends.tmux.subprocess.run")
def test_tmuxcmd_success(mock_run):
    """returncode=0: stdout returns parsed lines."""
    mock_run.return_value = MagicMock(returncode=0, stdout=b"line1\nline2\n", stderr=b"")
    cmd = TmuxCmd("/usr/bin/tmux", ["list-sessions"])
    assert cmd.stdout == ["line1", "line2"]


@patch("scry.backends.tmux.subprocess.run")
def test_tmuxcmd_nonzero_raises(mock_run):
    """returncode!=0: RuntimeError raised with stderr."""
    mock_run.return_value = MagicMock(returncode=1, stdout=b"", stderr=b"error: something went wrong")
    with pytest.raises(RuntimeError, match="nonzero"):
        TmuxCmd("/usr/bin/tmux", ["bad-command"])


@patch("scry.backends.tmux.subprocess.run")
def test_tmuxcmd_uses_given_binary(mock_run):
    """The binary passed to the constructor is what gets executed."""
    mock_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
    TmuxCmd("/opt/other/tmux", ["list-sessions"])
    assert mock_run.call_args[0][0][0] == "/opt/other/tmux"


@patch("scry.backends.tmux.subprocess.run")
def test_tmuxfmtcmd_parses_output(mock_run):
    """Multi-line __X__-separated output parsed into list of dicts."""
    mock_run.return_value = MagicMock(returncode=0, stdout=b"val1__X__val2\nval3__X__val4\n", stderr=b"")
    cmd = TmuxFmtCmd("/usr/bin/tmux", ["list-windows"], ["key1", "key2"])
    result = cmd.stdout
    assert len(result) == 2
    assert result[0] == {"key1": "val1", "key2": "val2"}
    assert result[1] == {"key1": "val3", "key2": "val4"}


@patch("scry.backends.tmux.subprocess.run")
def test_tmuxfmtcmd_single_line(mock_run):
    """Single line parsed correctly."""
    mock_run.return_value = MagicMock(returncode=0, stdout=b"only__X__line\n", stderr=b"")
    cmd = TmuxFmtCmd("/usr/bin/tmux", ["list-windows"], ["a", "b"])
    result = cmd.stdout
    assert len(result) == 1
    assert result[0] == {"a": "only", "b": "line"}


# --- TmuxBackend method tests ---


@patch("scry.backends.tmux.TmuxFmtCmd")
def test_list_windows_returns_sorted(mock_fmt, backend):
    """Windows returned sorted by window_name."""
    mock_fmt.return_value.stdout = [
        {"window_id": "@2", "window_name": "zeta", "window_active_clients": "0"},
        {"window_id": "@1", "window_name": "alpha", "window_active_clients": "0"},
    ]
    result = backend.list_windows("main")
    assert result[0]["window_name"] == "alpha"
    assert result[1]["window_name"] == "zeta"


@patch("scry.backends.tmux.TmuxFmtCmd")
def test_list_windows_no_server(mock_fmt, backend):
    """RuntimeError 'no server running' returns []."""
    mock_fmt.side_effect = RuntimeError("tmux returned nonzero with stderr: b'no server running'")
    assert backend.list_windows("main") == []


@patch("scry.backends.tmux.TmuxFmtCmd")
def test_list_windows_reraises_other_errors(mock_fmt, backend):
    """RuntimeErrors that aren't 'no server' propagate instead of UnboundLocalError."""
    mock_fmt.side_effect = RuntimeError("tmux returned nonzero with stderr: b'permission denied'")
    with pytest.raises(RuntimeError, match="permission denied"):
        backend.list_windows("main")


@patch("scry.backends.tmux.TmuxFmtCmd")
def test_list_sessions_returns_sorted(mock_fmt, backend):
    """Sessions returned sorted by session_name."""
    mock_fmt.return_value.stdout = [
        {"session_id": "$1", "session_name": "zebra", "session_attached": "0", "session_group": "main"},
        {"session_id": "$0", "session_name": "aardvark", "session_attached": "1", "session_group": "main"},
    ]
    result = backend.list_sessions()
    assert result[0]["session_name"] == "aardvark"
    assert result[1]["session_name"] == "zebra"


@patch("scry.backends.tmux.TmuxFmtCmd")
def test_list_sessions_no_server(mock_fmt, backend):
    """RuntimeError 'no server running' returns []."""
    mock_fmt.side_effect = RuntimeError("no server running")
    assert backend.list_sessions() == []


@patch("scry.backends.tmux.TmuxFmtCmd")
def test_list_sessions_reraises_other_errors(mock_fmt, backend):
    """RuntimeErrors that aren't 'no server' propagate."""
    mock_fmt.side_effect = RuntimeError("tmux returned nonzero with stderr: b'permission denied'")
    with pytest.raises(RuntimeError, match="permission denied"):
        backend.list_sessions()


def test_session_exists_true_and_false(backend):
    """Correctly reports existence based on session list."""
    with patch.object(backend, "list_sessions", return_value=[{"session_name": "main"}, {"session_name": "other"}]):
        assert backend.session_exists("main") is True
        assert backend.session_exists("nonexistent") is False


def test_window_exists_true_and_false(backend):
    """Correctly reports existence based on window list."""
    with patch.object(backend, "list_windows", return_value=[{"window_name": "alpha"}, {"window_name": "beta"}]):
        assert backend.window_exists("alpha", "main") is True
        assert backend.window_exists("missing", "main") is False


@patch("scry.backends.tmux.TmuxCmd")
def test_create_window_success(mock_cmd, backend):
    """Validates session exists, window doesn't, then creates."""
    with (
        patch.object(backend, "session_exists", return_value=True),
        patch.object(backend, "window_exists", return_value=False),
    ):
        backend.create_detached_window("newwin", "main")
    mock_cmd.assert_called_once()
    args = mock_cmd.call_args[0][1]
    assert "new-window" in args
    assert "newwin" in args


def test_create_window_no_session(backend):
    """Raises RuntimeError when session doesn't exist."""
    with patch.object(backend, "session_exists", return_value=False):
        with pytest.raises(RuntimeError, match="does not exist"):
            backend.create_detached_window("win", "nosuchgroup")


def test_create_window_duplicate(backend):
    """Raises RuntimeError when window already exists."""
    with (
        patch.object(backend, "session_exists", return_value=True),
        patch.object(backend, "window_exists", return_value=True),
    ):
        with pytest.raises(RuntimeError, match="already exists"):
            backend.create_detached_window("existing", "main")


@patch("scry.backends.tmux.TmuxCmd")
def test_rename_window(mock_cmd, backend):
    """Calls tmux with correct rename-window args."""
    backend.rename_window("@5", "newname", "main")
    args = mock_cmd.call_args[0][1]
    assert "rename-window" in args
    assert "newname" in args
    assert "main:@5" in args


@patch("scry.backends.tmux.TmuxCmd")
def test_ensure_session_group_uses_group_as_name(mock_cmd, backend):
    """ensure_session_group creates a session named after the group."""
    with patch.object(backend, "session_exists", return_value=False):
        backend.ensure_session_group("main")
    args = mock_cmd.call_args[0][1]
    assert "new-session" in args
    assert args.count("main") == 2  # -s main -t main


@patch("scry.backends.tmux.TmuxCmd")
def test_create_session_random_name(mock_cmd, backend):
    """Generates 8-digit name when no name provided."""
    with patch.object(backend, "session_exists", return_value=False):
        result = backend._create_detached_session("main")
    assert result.isdigit()
    assert len(result) == 8


@patch("scry.backends.tmux.TmuxCmd")
def test_attach_window_finds_unattached(mock_cmd, backend):
    """Finds unattached 8-digit session in correct group."""
    sessions = [
        {"session_id": "$0", "session_name": "main", "session_attached": "1", "session_group": "main"},
        {"session_id": "$1", "session_name": "12345678", "session_attached": "0", "session_group": "main"},
    ]
    with (
        patch.object(backend, "list_sessions", return_value=sessions),
        patch.object(backend, "_create_detached_session") as mock_create,
    ):
        backend.attach_window("@1", "main")
    mock_create.assert_not_called()
    args = mock_cmd.call_args[0][1]
    assert "attach-session" in args
    assert "$1:@1" in args


@patch("scry.backends.tmux.TmuxCmd")
def test_attach_window_creates_when_none_available(mock_cmd, backend):
    """Creates new session when all are attached."""
    sessions = [
        {"session_id": "$0", "session_name": "main", "session_attached": "1", "session_group": "main"},
        {"session_id": "$1", "session_name": "12345678", "session_attached": "1", "session_group": "main"},
    ]
    with (
        patch.object(backend, "list_sessions", return_value=sessions),
        patch.object(backend, "_create_detached_session", return_value="newses") as mock_create,
    ):
        backend.attach_window("@1", "main")
    mock_create.assert_called_once_with("main")
    args = mock_cmd.call_args[0][1]
    assert "newses:@1" in args
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_backend_tmux.py -v`
Expected: ERROR with `ModuleNotFoundError: No module named 'scry.backends.tmux'`

- [ ] **Step 3: Write the implementation**

Create `src/scry/backends/tmux.py` (bodies of `TmuxCmd._execute_cmd`, both `stdout` properties, and `_format_tmux_keys` are copied verbatim from `src/scry/tmuxcmd.py`):

```python
# -*- coding: utf-8 -*-
"""tmux backend: subprocess command wrappers and the TmuxBackend implementation."""

import logging
import random
import subprocess
from pathlib import Path
from typing import Dict, List

from scry.backends.base import MuxBackend
from scry.bin_utils import find_bin_in_path

_TMUX_FORMAT_SEPARATOR = "__X__"
""" str: Format separator to use for tmux -F format constructions
"""

_NO_SERVER_MARKERS = ("no server running", "error connecting to")
""" tuple: stderr fragments that mean "no tmux server yet", not a real error
"""

_LOGGER = logging.getLogger(__name__)


class TmuxCmd(object):
    """Run one tmux command; raise RuntimeError on nonzero exit.

    Attributes:
        _tmux_bin: Path to the tmux binary.
        _tmux_args: List of arguments to pass to tmux.
        _cmd_executed: Boolean indicating if the command has been executed.
        _cmd: CompletedProcess object containing the results of the command execution.
        _cwd: Path object representing the working directory for command execution.
    """

    def __init__(self, tmux_bin: str, cmd_args: List[str]):
        """Initialize and execute a tmux command.

        Args:
            tmux_bin: Fully qualified path of the tmux binary.
            cmd_args: List of arguments to pass to the tmux binary.
        """
        self._tmux_bin = tmux_bin
        self._tmux_args = cmd_args
        self._cmd_executed: bool = False
        self._cmd: subprocess.CompletedProcess = None
        self._cwd: Path = Path.home()

        self._execute_cmd()

    def _execute_cmd(self) -> None:
        cmd = subprocess.run(
            [self._tmux_bin] + self._tmux_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self._cwd,
            check=False,
        )

        _LOGGER.debug("executing %s", " ".join([self._tmux_bin] + self._tmux_args))
        _LOGGER.debug("stdout %s", cmd.stdout)

        if cmd.returncode != 0:
            _LOGGER.error("tmux returned nonzero with stderr: %s", cmd.stderr)
            raise RuntimeError(f"tmux returned nonzero with stderr: {cmd.stderr}")

        # Set the executed flag and save the CompletedProcess obj
        self._cmd = cmd
        self._cmd_executed = True

    @property
    def stdout(self) -> List:
        """
        Get the stdout of the tmux command.

        Returns:
            List[str]: The stdout of the tmux command.
        """
        if self._cmd_executed:
            stdout = self._cmd.stdout.decode("utf-8")
            return stdout.splitlines()

        raise ValueError("tmux command did not execute correctly; no stdout.")


class TmuxFmtCmd(TmuxCmd):
    """Like a regular TmuxCmd object, but we return a parsed stdout from a tmux format"""

    def __init__(self, tmux_bin: str, args: List[str], fmt_keys: List[str]):
        self._fmt_keys = fmt_keys

        fmt_string = self._format_tmux_keys(fmt_keys)
        args += ["-F", fmt_string]

        super(TmuxFmtCmd, self).__init__(tmux_bin, args)

    @staticmethod
    def _format_tmux_keys(fmt_keys: List[str]) -> str:
        """reformat keys to tmux-style '#{key}' strings"""
        fmt_keys = [f"#{{{key}}}" for key in fmt_keys]
        fmt_string = _TMUX_FORMAT_SEPARATOR.join(fmt_keys)
        return fmt_string

    @property
    def stdout(self) -> List[Dict[str, str]]:
        if self._cmd_executed:
            _ret = list()

            stdout = self._cmd.stdout.decode("utf-8")
            for line in stdout.splitlines():
                _LOGGER.debug("line: %s", line)
                line_vals = line.split(sep=_TMUX_FORMAT_SEPARATOR)

                # Create a dict using the fmt_keys as the keys
                _ret.append(dict(zip(self._fmt_keys, line_vals)))
            return _ret

        raise ValueError("tmux command did not execute correctly; no stdout.")


class TmuxBackend(MuxBackend):
    """MuxBackend implementation for tmux, using session groups.

    Multi-client support works via tmux session groups: throwaway sessions
    with random 8-digit names are created in the group so each client can
    attach to a different window of the same window set.
    """

    def __init__(self):
        self._bin = find_bin_in_path("tmux")

    def list_windows(self, session_group: str) -> List[Dict[str, str]]:
        """Return the group's windows sorted by window_name; [] if no server."""
        try:
            tmux_cmd = TmuxFmtCmd(
                self._bin,
                ["list-windows", "-t", session_group],
                ["window_id", "window_name", "window_active_clients"],
            )
        except RuntimeError as e:
            _LOGGER.error("tmux command failed: %s", str(e))
            if any(marker in str(e) for marker in _NO_SERVER_MARKERS):
                # This is okay. It just means there's no server yet.
                return []
            raise

        return sorted(tmux_cmd.stdout, key=lambda k: k["window_name"])

    def list_sessions(self) -> List[Dict[str, str]]:
        """Return all sessions sorted by session_name; [] if no server."""
        try:
            tmux_cmd = TmuxFmtCmd(
                self._bin,
                ["list-sessions"],
                ["session_id", "session_name", "session_attached", "session_group"],
            )
        except RuntimeError as e:
            if any(marker in str(e) for marker in _NO_SERVER_MARKERS):
                # This is okay. It just means there's no server yet.
                return []
            raise

        return sorted(tmux_cmd.stdout, key=lambda k: k["session_name"])

    def create_detached_window(self, window_name: str, session_group: str) -> None:
        """Create a new detached window in the group.

        Raises:
            RuntimeError: If the session group does not exist or the window
                already exists.
        """
        if not self.session_exists(session_group):
            raise RuntimeError(f"Session group {session_group} does not exist")

        if self.window_exists(window_name, session_group):
            raise RuntimeError(f"Window {window_name} already exists in session group {session_group}")

        TmuxCmd(self._bin, ["new-window", "-t", session_group, "-n", window_name, "-d"])

    def rename_window(self, window_id: str, new_name: str, session_group: str) -> None:
        """Rename a window in the group."""
        TmuxCmd(self._bin, ["rename-window", "-t", f"{session_group}:{window_id}", new_name])

    def attach_window(self, window_id: str, session_group: str) -> None:
        """Attach to a window via an unattached 8-digit session in the group.

        If no unattached numbered session exists, a new one is created first.
        """
        session_to_attach: str = None

        sessions = self.list_sessions()
        for session in sessions:
            if (
                session["session_group"] == session_group
                and session["session_name"].isdigit()
                and session["session_attached"] == "0"
                and len(session["session_name"]) == 8
            ):
                session_to_attach = session["session_id"]
                break

        if session_to_attach is None:
            # No unattached sessions found. Create a new one.
            session_to_attach = self._create_detached_session(session_group)

        TmuxCmd(self._bin, ["attach-session", "-t", ":".join([session_to_attach, window_id])])

    def ensure_session_group(self, session_group: str) -> None:
        """Create the group's base session (named after the group) if missing."""
        self._create_detached_session(session_group, session_name=session_group)

    def _create_detached_session(self, session_group: str, session_name: str = None) -> str:
        """Create a detached session in the group; random 8-digit name if none given.

        Returns:
            str: The name of the newly created session.
        """
        if session_name is None:
            session_name = str(random.randint(10000000, 99999999))
            while self.session_exists(session_name):
                session_name = str(random.randint(10000000, 99999999))

        TmuxCmd(self._bin, ["new-session", "-s", session_name, "-d", "-t", session_group])
        return session_name
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_backend_tmux.py -v`
Expected: 21 passed

- [ ] **Step 5: Full suite, lint, commit**

```bash
uv run pytest -q          # everything green; test_tmuxcmd.py still passes untouched
uv run ruff format . && uv run ruff check . --fix
git add src/scry/backends/tmux.py tests/test_backend_tmux.py
git commit -m "feat: add TmuxBackend implementing MuxBackend"
```

---

### Task 4: `backend` config key, `--backend` flag, and `get_backend` factory

**Files:**
- Modify: `src/scry/scry.py` (only `default_config` and `parse_args_and_configure`; call-site rewiring is Task 5)
- Modify: `src/scry/backends/__init__.py`
- Test: `tests/test_backends_factory.py`

**Interfaces:**
- Consumes: `TmuxBackend` (Task 3). References `scry.backends.zellij.ZellijBackend` via lazy import — that module arrives in Task 6, so the factory's zellij branch imports lazily and its test is added in Task 6.
- Produces: `get_backend(cfg: Dict) -> MuxBackend` raising `ValueError` for unknown backend names; `config["backend"]` defaulting to `"tmux"`; CLI `--backend`/`-b` with `choices=["tmux", "zellij"]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_backends_factory.py`:

```python
"""Tests for get_backend() and the backend config key."""

import sys
from unittest.mock import patch

import pytest

from scry.backends import get_backend
from scry.backends.tmux import TmuxBackend
from scry.scry import parse_args_and_configure


def test_get_backend_default_is_tmux():
    with patch("scry.backends.tmux.find_bin_in_path", return_value="/usr/bin/tmux"):
        backend = get_backend({})
    assert isinstance(backend, TmuxBackend)


def test_get_backend_tmux_explicit():
    with patch("scry.backends.tmux.find_bin_in_path", return_value="/usr/bin/tmux"):
        backend = get_backend({"backend": "tmux"})
    assert isinstance(backend, TmuxBackend)


def test_get_backend_unknown_raises_valueerror():
    with pytest.raises(ValueError, match="unknown backend"):
        get_backend({"backend": "screen"})


def test_config_backend_defaults_to_tmux():
    with patch.object(sys, "argv", ["scry"]):
        cfg = parse_args_and_configure()
    assert cfg["backend"] == "tmux"


def test_config_backend_cli_flag():
    with patch.object(sys, "argv", ["scry", "--backend", "zellij"]):
        cfg = parse_args_and_configure()
    assert cfg["backend"] == "zellij"


def test_config_backend_short_flag():
    with patch.object(sys, "argv", ["scry", "-b", "zellij"]):
        cfg = parse_args_and_configure()
    assert cfg["backend"] == "zellij"


def test_config_backend_rejects_unknown_cli_value():
    with patch.object(sys, "argv", ["scry", "--backend", "screen"]):
        with pytest.raises(SystemExit):
            parse_args_and_configure()
```

Note: `parse_args_and_configure()` reads `~/.scry.yml` if present; the existing tests in `tests/test_config.py` already tolerate this. If the CLI-flag assertions above could be affected by a stray `backend:` key in the real user config, patch `os.path.exists` to return `False` inside the config tests, following whatever pattern `tests/test_config.py` already uses — check it before writing, and match it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_backends_factory.py -v`
Expected: ImportError (`cannot import name 'get_backend'`)

- [ ] **Step 3: Implement the factory**

Replace the content of `src/scry/backends/__init__.py` with:

```python
# -*- coding: utf-8 -*-
"""Multiplexer backend implementations and the factory that selects one."""

from typing import Dict

from scry.backends.base import MuxBackend


def get_backend(cfg: Dict) -> MuxBackend:
    """Instantiate the multiplexer backend named by cfg["backend"].

    Args:
        cfg: The scry config dict; the "backend" key selects the backend
            (missing key defaults to tmux).

    Returns:
        MuxBackend: The instantiated backend.

    Raises:
        ValueError: Unknown backend name, or the backend's binary is not in
            PATH (raised by find_bin_in_path in the backend constructor).
    """
    backend_name = cfg.get("backend", "tmux")

    if backend_name == "tmux":
        from scry.backends.tmux import TmuxBackend

        return TmuxBackend()
    if backend_name == "zellij":
        from scry.backends.zellij import ZellijBackend

        return ZellijBackend()

    raise ValueError(f"unknown backend {backend_name!r}: expected 'tmux' or 'zellij'")
```

- [ ] **Step 4: Add the config key and flag in `src/scry/scry.py`**

In `default_config` (currently ends with `"hide_prefixes": False, ...`), add:

```python
    "backend": "tmux",  # Terminal multiplexer backend: "tmux" or "zellij"
```

In `parse_args_and_configure()`, after the `--hide-prefixes` `parser.add_argument(...)` block, add:

```python
    parser.add_argument(
        "--backend",
        "-b",
        type=str,
        choices=["tmux", "zellij"],
        help="Terminal multiplexer backend to use.",
    )
```

In the "Update config with command-line arguments" section, after the `if args.hide_prefixes:` block, add:

```python
    if args.backend is not None:
        cfg["backend"] = args.backend
```

(A bad `backend:` value coming from `~/.scry.yml` rather than the CLI is caught by `get_backend()`'s `ValueError` — wired to a clean exit in Task 5.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_backends_factory.py -v`
Expected: 7 passed

- [ ] **Step 6: Full suite, lint, commit**

```bash
uv run pytest -q
uv run ruff format . && uv run ruff check . --fix
git add src/scry/backends/__init__.py src/scry/scry.py tests/test_backends_factory.py
git commit -m "feat: add backend config key, --backend flag, get_backend factory"
```

---

### Task 5: Rewire scry.py to the backend object; delete tmuxcmd.py

**Files:**
- Modify: `src/scry/scry.py`
- Modify: `tests/conftest.py` (comment only)
- Modify: `tests/test_functional.py`, `tests/test_commands.py`, `tests/test_smoke.py`, `tests/test_file_operations.py`, `tests/test_pure_functions.py` (patch targets)
- Delete: `src/scry/tmuxcmd.py`, `tests/test_tmuxcmd.py`

**Interfaces:**
- Consumes: `get_backend` (Task 4), `MuxBackend` methods (Task 2/3).
- Produces: module-level `scry.scry.backend` (a `MuxBackend` instance) — the object all tests patch from now on.

- [ ] **Step 1: Rewire `src/scry/scry.py`**

Replace the import block:

```python
from scry.tmuxcmd import (
    tmux_attach_window,
    tmux_create_detached_session,
    tmux_create_detached_window,
    tmux_list_sessions,
    tmux_list_windows,
    tmux_rename_window,
    tmux_window_exists,
)
```

with:

```python
from scry.backends import get_backend
```

Immediately after the existing `config = parse_args_and_configure()` line, add:

```python
# Instantiate the configured multiplexer backend (module-level, like config)
try:
    backend = get_backend(config)
except ValueError as e:
    _LOGGER.error("%s", e)
    sys.exit(1)
```

Then apply this call-site mapping everywhere in the file:

| Old | New |
|---|---|
| `tmux_list_windows(x)` | `backend.list_windows(x)` |
| `tmux_list_sessions()` | `backend.list_sessions()` |
| `tmux_create_detached_window(a, b)` | `backend.create_detached_window(a, b)` |
| `tmux_rename_window(a, b, c)` | `backend.rename_window(a, b, c)` |
| `tmux_window_exists(a, b)` | `backend.window_exists(a, b)` |
| `tmux_attach_window(a, b)` | `backend.attach_window(a, b)` |
| `tmux_create_detached_session(session_group, session_name=session_group)` | `backend.ensure_session_group(session_group)` |

The last row occurs only inside `ensure_session_group_exists()`, which becomes:

```python
def ensure_session_group_exists(session_group: str) -> bool:
    """Ensure the session group exists, creating it if necessary.

    Args:
        session_group: The name of the session group to check/create.

    Returns:
        bool: True if the session group exists or was created, False if creation failed.
    """
    sessions = backend.list_sessions()
    if not any(session["session_name"] == session_group for session in sessions):
        try:
            backend.ensure_session_group(session_group)
            return False
        except RuntimeError:
            # TODO - something more graceful here
            return False
    return True
```

Verify no references remain: `grep -n "tmux_" src/scry/scry.py` → no output.

- [ ] **Step 2: Update test patch targets**

Mechanical mapping across all test files (`grep -rln "scry.scry.tmux_" tests/` to find them):

| Old patch target | New patch target |
|---|---|
| `scry.scry.tmux_list_windows` | `scry.scry.backend.list_windows` |
| `scry.scry.tmux_list_sessions` | `scry.scry.backend.list_sessions` |
| `scry.scry.tmux_create_detached_window` | `scry.scry.backend.create_detached_window` |
| `scry.scry.tmux_rename_window` | `scry.scry.backend.rename_window` |
| `scry.scry.tmux_window_exists` | `scry.scry.backend.window_exists` |
| `scry.scry.tmux_attach_window` | `scry.scry.backend.attach_window` |
| `scry.scry.tmux_create_detached_session` | `scry.scry.backend.ensure_session_group` |

(`patch("scry.scry.backend.list_windows")` patches the attribute on the module-level backend instance — the decorator/context-manager usage in the tests doesn't otherwise change.) If any test asserts on `tmux_create_detached_session` call args (e.g. `session_name=` kwarg), update the assertion to `ensure_session_group`'s single-argument signature.

- [ ] **Step 3: Update `tests/conftest.py` comment and delete dead modules**

The conftest's import-time patches are still required (`scry.scry` builds the backend at import, which calls `find_bin_in_path`). Update the comment block to say so:

```python
# Neutralize import-time side effects BEFORE any scry module is imported:
# 1. sys.argv — prevents argparse from reading test runner args
# 2. find_bin_in_path — scry.scry instantiates the backend at import time,
#    which resolves the multiplexer binary in the backend constructor
```

Then:

```bash
git rm src/scry/tmuxcmd.py tests/test_tmuxcmd.py
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all green. Also verify nothing references the old module: `grep -rn "tmuxcmd" src/ tests/` → no output.

- [ ] **Step 5: Manual smoke test against a live tmux server (throwaway group only)**

```bash
uv run scry --session_group scry-test-$$ --backend tmux
# In the TUI: 'n testwin' to create+attach a window, detach (prefix-d), 'q' to quit.
tmux kill-session -t scry-test-<the pid>   # plus any 8-digit sessions it created:
tmux list-sessions   # kill any 8-digit-named sessions belonging to the test group
```

Expected: window list renders, create/attach/detach work exactly as on main. **Do not run without `--session_group`.**

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff format . && uv run ruff check . --fix
git add -A
git commit -m "refactor: route scry.py through MuxBackend; remove tmuxcmd.py"
```

---

### Task 6: ZellijBackend

Implements the spec's zellij semantics table. **Before starting, read the "Spike results" section of the spec** (written in Task 1): if the rename sequence failed detached, use the fallback `rename_window` shown in Step 3; if the user chose a different attach variant, apply the recorded Decision instead of the default.

**Files:**
- Create: `src/scry/backends/zellij.py`
- Test: `tests/test_backend_zellij.py`
- Modify: `tests/test_backends_factory.py` (add the zellij factory test)

**Interfaces:**
- Consumes: `MuxBackend` (Task 2), `find_bin_in_path`.
- Produces: `ZellijBackend()` (resolves binary via `find_bin_in_path("zellij")` in `__init__`); `ZellijCmd(zellij_bin: str, cmd_args: List[str], capture: bool = True)` with `.stdout` property, used by Task 7's contract tests.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_backend_zellij.py` (stdout/stderr fixtures are real zellij 0.44.3 output):

```python
"""Tests for the zellij backend."""

from unittest.mock import MagicMock, patch

import pytest

from scry.backends.zellij import ZellijBackend, ZellijCmd


@pytest.fixture
def backend():
    with patch("scry.backends.zellij.find_bin_in_path", return_value="/home/user/bin/zellij"):
        return ZellijBackend()


def _completed(returncode=0, stdout=b"", stderr=b""):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


# --- ZellijCmd class tests ---


@patch("scry.backends.zellij.subprocess.run")
def test_zellijcmd_success(mock_run):
    """returncode=0: stdout returns parsed lines."""
    mock_run.return_value = _completed(stdout=b"line1\nline2\n")
    cmd = ZellijCmd("/home/user/bin/zellij", ["list-sessions"])
    assert cmd.stdout == ["line1", "line2"]


@patch("scry.backends.zellij.subprocess.run")
def test_zellijcmd_nonzero_raises(mock_run):
    """returncode!=0: RuntimeError raised with stderr."""
    mock_run.return_value = _completed(returncode=1, stderr=b"Some other error")
    with pytest.raises(RuntimeError, match="nonzero"):
        ZellijCmd("/home/user/bin/zellij", ["action", "bad"])


@patch("scry.backends.zellij.subprocess.run")
def test_zellijcmd_no_capture_runs_without_pipes(mock_run):
    """capture=False leaves stdout/stderr connected to the tty (attach needs this)."""
    mock_run.return_value = _completed()
    ZellijCmd("/home/user/bin/zellij", ["attach", "main"], capture=False)
    kwargs = mock_run.call_args[1]
    assert kwargs["stdout"] is None
    assert kwargs["stderr"] is None


# --- ZellijBackend method tests ---


@patch("scry.backends.zellij.subprocess.run")
def test_list_windows_names_are_id_and_name(mock_run, backend):
    """Tab names double as window_id and window_name; active clients always 0; sorted."""
    mock_run.return_value = _completed(stdout=b"zeta\nTab #1\nalpha\n")
    windows = backend.list_windows("main")
    assert windows == [
        {"window_id": "Tab #1", "window_name": "Tab #1", "window_active_clients": "0"},
        {"window_id": "alpha", "window_name": "alpha", "window_active_clients": "0"},
        {"window_id": "zeta", "window_name": "zeta", "window_active_clients": "0"},
    ]
    argv = mock_run.call_args[0][0]
    assert argv == ["/home/user/bin/zellij", "--session", "main", "action", "query-tab-names"]


@patch("scry.backends.zellij.subprocess.run")
def test_list_windows_no_session_returns_empty(mock_run, backend):
    """'There is no active session!' means no windows yet, not an error."""
    mock_run.return_value = _completed(returncode=1, stderr=b"There is no active session!\n")
    assert backend.list_windows("main") == []


@patch("scry.backends.zellij.subprocess.run")
def test_list_windows_reraises_other_errors(mock_run, backend):
    mock_run.return_value = _completed(returncode=1, stderr=b"Permission denied")
    with pytest.raises(RuntimeError, match="Permission denied"):
        backend.list_windows("main")


@patch("scry.backends.zellij.subprocess.run")
def test_list_sessions_parses_and_normalizes(mock_run, backend):
    """First whitespace-separated token is the session name; group mirrors name."""
    mock_run.return_value = _completed(stdout=b"work [Created 2h ago] \nmain [Created 5s ago] \n")
    sessions = backend.list_sessions()
    assert sessions == [
        {"session_id": "main", "session_name": "main", "session_attached": "0", "session_group": "main"},
        {"session_id": "work", "session_name": "work", "session_attached": "0", "session_group": "work"},
    ]
    argv = mock_run.call_args[0][0]
    assert argv == ["/home/user/bin/zellij", "list-sessions", "--no-formatting"]


@patch("scry.backends.zellij.subprocess.run")
def test_list_sessions_skips_exited(mock_run, backend):
    """EXITED (resurrectable) sessions are treated as missing."""
    mock_run.return_value = _completed(
        stdout=b"main [Created 5s ago] \ndead [Created 3d ago] (EXITED - attach to resurrect)\n"
    )
    sessions = backend.list_sessions()
    assert [s["session_name"] for s in sessions] == ["main"]


@patch("scry.backends.zellij.subprocess.run")
def test_list_sessions_none_returns_empty(mock_run, backend):
    """'No active zellij sessions found.' maps to []."""
    mock_run.return_value = _completed(returncode=1, stderr=b"No active zellij sessions found.\n")
    assert backend.list_sessions() == []


@patch("scry.backends.zellij.subprocess.run")
def test_ensure_session_group(mock_run, backend):
    mock_run.return_value = _completed()
    backend.ensure_session_group("main")
    argv = mock_run.call_args[0][0]
    assert argv == ["/home/user/bin/zellij", "attach", "--create-background", "main"]


@patch("scry.backends.zellij.subprocess.run")
def test_create_window_success(mock_run, backend):
    mock_run.return_value = _completed(stdout=b"2\n")
    with (
        patch.object(backend, "session_exists", return_value=True),
        patch.object(backend, "window_exists", return_value=False),
    ):
        backend.create_detached_window("newwin", "main")
    argv = mock_run.call_args[0][0]
    assert argv == ["/home/user/bin/zellij", "--session", "main", "action", "new-tab", "--name", "newwin"]


def test_create_window_no_session(backend):
    with patch.object(backend, "session_exists", return_value=False):
        with pytest.raises(RuntimeError, match="does not exist"):
            backend.create_detached_window("win", "nosuchgroup")


def test_create_window_duplicate(backend):
    """Zellij allows duplicate tab names; scry must not."""
    with (
        patch.object(backend, "session_exists", return_value=True),
        patch.object(backend, "window_exists", return_value=True),
    ):
        with pytest.raises(RuntimeError, match="already exists"):
            backend.create_detached_window("existing", "main")


@patch("scry.backends.zellij.subprocess.run")
def test_rename_window_focuses_then_renames(mock_run, backend):
    mock_run.return_value = _completed()
    backend.rename_window("oldname", "newname", "main")
    calls = [c[0][0] for c in mock_run.call_args_list]
    assert calls[0] == ["/home/user/bin/zellij", "--session", "main", "action", "go-to-tab-name", "oldname"]
    assert calls[1] == ["/home/user/bin/zellij", "--session", "main", "action", "rename-tab", "newname"]


@patch("scry.backends.zellij.subprocess.run")
def test_attach_window_focuses_tab_then_attaches(mock_run, backend):
    """go-to-tab-name (captured), then blocking attach (uncaptured, needs the tty)."""
    mock_run.return_value = _completed()
    backend.attach_window("mytab", "main")
    calls = mock_run.call_args_list
    assert calls[0][0][0] == ["/home/user/bin/zellij", "--session", "main", "action", "go-to-tab-name", "mytab"]
    assert calls[1][0][0] == ["/home/user/bin/zellij", "attach", "main"]
    assert calls[1][1]["stdout"] is None  # attach must own the tty
```

Also append to `tests/test_backends_factory.py`:

```python
def test_get_backend_zellij():
    from scry.backends.zellij import ZellijBackend

    with patch("scry.backends.zellij.find_bin_in_path", return_value="/home/user/bin/zellij"):
        backend = get_backend({"backend": "zellij"})
    assert isinstance(backend, ZellijBackend)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_backend_zellij.py -v`
Expected: ERROR with `ModuleNotFoundError: No module named 'scry.backends.zellij'`

- [ ] **Step 3: Write the implementation**

Create `src/scry/backends/zellij.py`:

```python
# -*- coding: utf-8 -*-
"""zellij backend: subprocess command wrapper and the ZellijBackend implementation."""

import logging
import subprocess
from pathlib import Path
from typing import Dict, List

from scry.backends.base import MuxBackend
from scry.bin_utils import find_bin_in_path

_NO_SESSION_MARKERS = ("There is no active session", "No active zellij sessions found")
""" tuple: stderr fragments that mean "no session/server yet", not a real error
"""

_LOGGER = logging.getLogger(__name__)


class ZellijCmd(object):
    """Run one zellij command; raise RuntimeError on nonzero exit.

    Attributes:
        _zellij_bin: Path to the zellij binary.
        _zellij_args: List of arguments to pass to zellij.
        _cmd_executed: Boolean indicating if the command has been executed.
        _cmd: CompletedProcess object containing the results of the command execution.
        _cwd: Path object representing the working directory for command execution.
    """

    def __init__(self, zellij_bin: str, cmd_args: List[str], capture: bool = True):
        """Initialize and execute a zellij command.

        Args:
            zellij_bin: Fully qualified path of the zellij binary.
            cmd_args: List of arguments to pass to the zellij binary.
            capture: Pipe stdout/stderr (True) or leave them on the tty
                (False — required for `zellij attach`).
        """
        self._zellij_bin = zellij_bin
        self._zellij_args = cmd_args
        self._capture = capture
        self._cmd_executed: bool = False
        self._cmd: subprocess.CompletedProcess = None
        self._cwd: Path = Path.home()

        self._execute_cmd()

    def _execute_cmd(self) -> None:
        pipe = subprocess.PIPE if self._capture else None
        cmd = subprocess.run(
            [self._zellij_bin] + self._zellij_args,
            stdout=pipe,
            stderr=pipe,
            cwd=self._cwd,
            check=False,
        )

        _LOGGER.debug("executing %s", " ".join([self._zellij_bin] + self._zellij_args))
        _LOGGER.debug("stdout %s", cmd.stdout)

        if cmd.returncode != 0:
            _LOGGER.error("zellij returned nonzero with stderr: %s", cmd.stderr)
            raise RuntimeError(f"zellij returned nonzero with stderr: {cmd.stderr}")

        self._cmd = cmd
        self._cmd_executed = True

    @property
    def stdout(self) -> List:
        """
        Get the stdout of the zellij command.

        Returns:
            List[str]: The stdout of the zellij command.
        """
        if self._cmd_executed and self._cmd.stdout is not None:
            stdout = self._cmd.stdout.decode("utf-8")
            return stdout.splitlines()

        raise ValueError("zellij command did not execute correctly; no stdout.")


class ZellijBackend(MuxBackend):
    """MuxBackend implementation for zellij.

    The session_group maps to a single zellij session; tabs are windows and
    the tab name doubles as the window_id (zellij 0.44 cannot enumerate
    stable tab IDs). All clients attach to the same session, relying on
    zellij's default multiplayer behavior for independent per-client focus.
    Per-tab attached-client counts are unavailable: window_active_clients is
    always "0" (no '#' indicator under zellij).
    """

    def __init__(self):
        self._bin = find_bin_in_path("zellij")

    def _action(self, session_group: str, *action_args: str) -> ZellijCmd:
        return ZellijCmd(self._bin, ["--session", session_group, "action", *action_args])

    def list_windows(self, session_group: str) -> List[Dict[str, str]]:
        """Return the session's tabs as windows sorted by name; [] if no session."""
        try:
            cmd = self._action(session_group, "query-tab-names")
        except RuntimeError as e:
            _LOGGER.error("zellij command failed: %s", str(e))
            if any(marker in str(e) for marker in _NO_SESSION_MARKERS):
                # This is okay. It just means there's no session yet.
                return []
            raise

        windows = [
            {"window_id": name, "window_name": name, "window_active_clients": "0"}
            for name in cmd.stdout
            if name.strip()
        ]
        return sorted(windows, key=lambda k: k["window_name"])

    def list_sessions(self) -> List[Dict[str, str]]:
        """Return live sessions sorted by name; EXITED sessions are treated as missing."""
        try:
            cmd = ZellijCmd(self._bin, ["list-sessions", "--no-formatting"])
        except RuntimeError as e:
            if any(marker in str(e) for marker in _NO_SESSION_MARKERS):
                return []
            raise

        sessions = []
        for line in cmd.stdout:
            if not line.strip() or "EXITED" in line:
                continue
            name = line.split()[0]
            # zellij has no session groups or a per-session attached count we
            # can query remotely; group mirrors the name, attached is unknown.
            sessions.append(
                {
                    "session_id": name,
                    "session_name": name,
                    "session_attached": "0",
                    "session_group": name,
                }
            )
        return sorted(sessions, key=lambda k: k["session_name"])

    def create_detached_window(self, window_name: str, session_group: str) -> None:
        """Create a new tab in the session.

        Raises:
            RuntimeError: If the session does not exist or a tab with this
                name already exists (zellij itself allows duplicates; scry
                requires unique names because the name is the window_id).
        """
        if not self.session_exists(session_group):
            raise RuntimeError(f"Session group {session_group} does not exist")

        if self.window_exists(window_name, session_group):
            raise RuntimeError(f"Window {window_name} already exists in session group {session_group}")

        self._action(session_group, "new-tab", "--name", window_name)

    def rename_window(self, window_id: str, new_name: str, session_group: str) -> None:
        """Rename a tab by focusing it, then renaming the focused tab."""
        self._action(session_group, "go-to-tab-name", window_id)
        self._action(session_group, "rename-tab", new_name)

    def attach_window(self, window_id: str, session_group: str) -> None:
        """Focus the tab, then attach this client to the session (blocks until detach)."""
        self._action(session_group, "go-to-tab-name", window_id)
        ZellijCmd(self._bin, ["attach", session_group], capture=False)

    def ensure_session_group(self, session_group: str) -> None:
        """Create the session, detached, if it does not already exist."""
        ZellijCmd(self._bin, ["attach", "--create-background", session_group])
```

**Spike-result adjustments (apply per the spec's recorded Decision):**
- If the detached rename sequence failed in Task 1 Part A, replace `rename_window`'s body with:

```python
        raise RuntimeError("window rename is not supported with the zellij backend")
```

- If the user chose to drop pre-focus on attach (spike outcome (b), user preference), delete the `go-to-tab-name` line from `attach_window` and note the limitation in Task 8's docs.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_backend_zellij.py tests/test_backends_factory.py -v`
Expected: all passed (15 zellij + 8 factory, minus any tests replaced by spike adjustments — if rename became unsupported, replace `test_rename_window_focuses_then_renames` with a `pytest.raises(RuntimeError, match="not supported")` assertion).

- [ ] **Step 5: Manual smoke test against live zellij (throwaway session only)**

```bash
uv run scry --backend zellij --session_group scry-test-$$
# In the TUI: 'n testwin' → creates tab and attaches; detach with Ctrl-o d;
# verify the list shows both tabs; attach by index; 'q' to quit.
~/bin/zellij kill-session scry-test-<the pid>
~/bin/zellij delete-session scry-test-<the pid> --force
```

Expected: window list renders tabs, create/attach/detach/switch work, no `#` markers appear.

- [ ] **Step 6: Full suite, lint, commit**

```bash
uv run pytest -q
uv run ruff format . && uv run ruff check . --fix
git add src/scry/backends/zellij.py tests/test_backend_zellij.py tests/test_backends_factory.py
git commit -m "feat: add ZellijBackend"
```

---

### Task 7: Cross-backend contract tests

Locks the "seamless" guarantee into CI: the same interface call against either backend yields identically-shaped results.

**Files:**
- Test: `tests/test_backend_contract.py`

**Interfaces:**
- Consumes: `TmuxBackend` (Task 3), `ZellijBackend` (Task 6).
- Produces: nothing (tests only).

- [ ] **Step 1: Write the tests**

Create `tests/test_backend_contract.py`:

```python
"""Contract tests: every backend returns the same normalized shapes."""

from unittest.mock import MagicMock, patch

import pytest

from scry.backends.base import MuxBackend
from scry.backends.tmux import TmuxBackend
from scry.backends.zellij import ZellijBackend

WINDOW_KEYS = {"window_id", "window_name", "window_active_clients"}
SESSION_KEYS = {"session_id", "session_name", "session_attached", "session_group"}

# Per-backend canned CLI output producing windows alpha/beta and session main.
CASES = {
    "tmux": {
        "cls": TmuxBackend,
        "module": "scry.backends.tmux",
        "windows_stdout": b"@2__X__beta__X__1\n@1__X__alpha__X__0\n",
        "sessions_stdout": b"$0__X__main__X__1__X__main\n",
        "no_server_stderr": b"no server running on /tmp/tmux-1000/default",
    },
    "zellij": {
        "cls": ZellijBackend,
        "module": "scry.backends.zellij",
        "windows_stdout": b"beta\nalpha\n",
        "sessions_stdout": b"main [Created 5s ago] \n",
        "no_server_stderr": b"There is no active session!\n",
    },
}


@pytest.fixture(params=sorted(CASES))
def case(request):
    params = CASES[request.param]
    with patch(f"{params['module']}.find_bin_in_path", return_value="/usr/bin/mux"):
        backend = params["cls"]()
    return backend, params


def _completed(returncode=0, stdout=b"", stderr=b""):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


def test_is_muxbackend(case):
    backend, _ = case
    assert isinstance(backend, MuxBackend)


def test_list_windows_shape_and_sorting(case):
    backend, params = case
    with patch(f"{params['module']}.subprocess.run", return_value=_completed(stdout=params["windows_stdout"])):
        windows = backend.list_windows("main")
    assert [w["window_name"] for w in windows] == ["alpha", "beta"]
    for window in windows:
        assert set(window) == WINDOW_KEYS
        assert all(isinstance(v, str) for v in window.values())


def test_list_sessions_shape(case):
    backend, params = case
    with patch(f"{params['module']}.subprocess.run", return_value=_completed(stdout=params["sessions_stdout"])):
        sessions = backend.list_sessions()
    assert [s["session_name"] for s in sessions] == ["main"]
    for session in sessions:
        assert set(session) == SESSION_KEYS
        assert all(isinstance(v, str) for v in session.values())


def test_list_windows_empty_when_no_server(case):
    backend, params = case
    with patch(
        f"{params['module']}.subprocess.run",
        return_value=_completed(returncode=1, stderr=params["no_server_stderr"]),
    ):
        assert backend.list_windows("main") == []


def test_unexpected_failure_raises_runtime_error(case):
    backend, params = case
    with patch(
        f"{params['module']}.subprocess.run",
        return_value=_completed(returncode=1, stderr=b"catastrophic failure"),
    ):
        with pytest.raises(RuntimeError, match="catastrophic failure"):
            backend.list_windows("main")


def test_create_window_rejects_missing_group(case):
    backend, _ = case
    with patch.object(backend, "session_exists", return_value=False):
        with pytest.raises(RuntimeError, match="does not exist"):
            backend.create_detached_window("w", "nogroup")


def test_create_window_rejects_duplicate(case):
    backend, _ = case
    with (
        patch.object(backend, "session_exists", return_value=True),
        patch.object(backend, "window_exists", return_value=True),
    ):
        with pytest.raises(RuntimeError, match="already exists"):
            backend.create_detached_window("w", "main")
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/test_backend_contract.py -v`
Expected: 14 passed (7 tests × 2 backends). These should pass immediately — any failure means a backend violates the contract; fix the backend, not the test.

- [ ] **Step 3: Full suite, lint, commit**

```bash
uv run pytest -q
uv run ruff format . && uv run ruff check . --fix
git add tests/test_backend_contract.py
git commit -m "test: add cross-backend contract tests"
```

---

### Task 8: Documentation

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: everything above (documents final behavior, including spike-decided variants).
- Produces: nothing (docs only).

- [ ] **Step 1: Update README.md**

- Line 6 intro: change "An interactive tmux window manager…" to "An interactive terminal-multiplexer window manager (tmux by default, zellij optional) with session grouping support…".
- **Prerequisites** (line ~23): change the `tmux` bullet to `tmux (default backend) and/or zellij >= 0.44`.
- **Command-line Options** section: add `- `--backend`/`-b`: multiplexer backend, `tmux` (default) or `zellij``.
- **Configuration File** example: add `backend: tmux` with a comment listing allowed values.
- **Project Structure** (line ~141): replace the `tmuxcmd.py` bullet with `backends/` — multiplexer backends (`base.py` interface, `tmux.py`, `zellij.py`).
- Add a new subsection under **Usage** titled **Zellij backend** documenting the differences (match the wording to the spike Decision recorded in the spec):
  - windows map to zellij tabs; all clients share one zellij session (multiplayer)
  - no `#` attached-client indicator
  - window identity is the tab name: renaming a window resets its history-highlight position
  - rename caveat (either "rename briefly focuses the tab" or "rename is not supported", per spike)
  - any focus-yank limitation the spike recorded

- [ ] **Step 2: Update CLAUDE.md**

- "What This Is": mention configurable backend (tmux default, zellij optional).
- Architecture section: replace the `tmuxcmd.py` layer-3 bullet with the `backends/` package (`MuxBackend` ABC in `base.py`, `TmuxBackend`, `ZellijBackend`, `get_backend()` factory selected by `config["backend"]`; binary lookup happens in backend constructors, not at import).
- Key Design Decisions: add a bullet on the backend abstraction and the zellij degradations; keep the session-group bullet but note it's tmux-specific (zellij maps the group to a single session).
- Testing bullet: update the test count to the real number from `uv run pytest -q` output.

- [ ] **Step 3: Final verification and commit**

```bash
uv run pytest -q                       # note the final count for CLAUDE.md
uv run ruff format . && uv run ruff check . --fix
git add README.md CLAUDE.md
git commit -m "docs: document configurable mux backend"
```

---

## Final acceptance checklist (after all tasks)

- [ ] `uv run pytest -q` green.
- [ ] `uv run scry` (no flags) behaves exactly as before against tmux — verify with a throwaway `--session_group scry-test-*` only.
- [ ] `uv run scry --backend zellij --session_group scry-test-*` provides the full loop: list, create (`n`), attach, detach, swap (`s`), rename (`r`, or clean error per spike), dump/load (`d`/`l`), quit.
- [ ] `~/.scry.yml` with `backend: zellij` selects zellij without any flag.
- [ ] Invalid backend value exits with a clear error, not a traceback.
- [ ] No stray `scry-test-*` tmux/zellij sessions left running.
