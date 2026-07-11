# -*- coding: utf-8 -*-
"""zellij backend: subprocess command wrapper and the ZellijBackend implementation."""

import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, List

from scry.backends.base import MuxBackend
from scry.bin_utils import find_bin_in_path

_NO_SESSION_MARKERS = ("There is no active session", "No active zellij sessions found")
""" tuple: stderr fragments that mean "no session/server yet", not a real error
"""

_ATTACH_REFOCUS_DELAYS = (0.4, 1.2)
""" tuple: seconds after launching attach to re-fire go-to-tab-name; remote focus
actions no-op with zero clients, so the first client can only be directed after
it has connected
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
        """Renaming is unsupported: zellij's rename-tab silently no-ops without an
        attached client (verified against 0.44.3), so fail loudly instead."""
        raise RuntimeError("window rename is not supported with the zellij backend")

    def _refocus_tab(self, window_id: str, session_group: str) -> None:
        """Re-fire go-to-tab-name after the attaching client has had time to connect.

        Stops on the first failure (e.g. the client detached again); directing
        focus is best-effort by then.
        """
        for delay in _ATTACH_REFOCUS_DELAYS:
            time.sleep(delay)
            try:
                self._action(session_group, "go-to-tab-name", window_id)
            except RuntimeError:
                return

    def attach_window(self, window_id: str, session_group: str) -> None:
        """Focus the tab, then attach this client to the session (blocks until detach).

        The pre-focus directs the new client when other clients are already
        attached (and, as a zellij 0.44 limitation, also pulls the session's
        first client to this tab); with zero clients it is a no-op, so a
        daemon thread re-fires the focus action once this client has connected.
        """
        self._action(session_group, "go-to-tab-name", window_id)
        threading.Thread(target=self._refocus_tab, args=(window_id, session_group), daemon=True).start()
        ZellijCmd(self._bin, ["attach", session_group], capture=False)

    def ensure_session_group(self, session_group: str) -> None:
        """Create the session, detached, if it does not already exist."""
        ZellijCmd(self._bin, ["attach", "--create-background", session_group])
