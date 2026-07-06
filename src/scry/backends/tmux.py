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
        if not self.session_exists(session_group):
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
