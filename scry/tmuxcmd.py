# -*- coding: utf-8 -*-
"""module for running tmux commands and parsing output thereof."""

import logging
import random
import subprocess
from typing import Dict, List

from scry.bin_utils import find_bin_in_path

tmux_binary = find_bin_in_path("tmux")
""" str: fully qualified path of tmux binary
"""

_TMUX_FORMAT_SEPARATOR = "__SEPARATOR__"
""" str: Format separator to use for tmux -F format constructions
"""

_LOGGER = logging.getLogger(__name__)


class TmuxCmd(object):
    def __init__(self, cmd_args: List[str]):
        """

        Args:
            cmd_args: arguments to pass to tmux binary
        """

        self._tmux_bin = tmux_binary
        self._tmux_args = cmd_args
        self._cmd_executed: bool = False
        self._cmd: subprocess.CompletedProcess = None

        self._execute_cmd()

    def _execute_cmd(self) -> None:
        cmd = subprocess.run([self._tmux_bin] + self._tmux_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        _LOGGER.debug(f"{cmd.stdout}")

        if cmd.returncode != 0:
            raise RuntimeError(f"tmux returned nonzero with stderr: {cmd.stderr}")

        # Set the executed flag and save the CompletedProcess obj
        self._cmd = cmd
        self._cmd_executed = True

    @property
    def stdout(self) -> List:
        if self._cmd_executed:
            stdout = self._cmd.stdout.decode("utf-8")
            return stdout.splitlines()

        else:
            raise ValueError("tmux command did not execute correctly; no stdout.")


class TmuxFmtCmd(TmuxCmd):
    """Like a regular TmuxCmd object, but we return a parsed stdout from a tmux format"""

    def __init__(self, args: List[str], fmt_keys: List[str]):
        self._fmt_keys = fmt_keys

        fmt_string = self._format_tmux_keys(fmt_keys)
        args += ["-F", fmt_string]

        super(TmuxFmtCmd, self).__init__(args)

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
                _LOGGER.debug(f"line: {line}")
                line_vals = line.split(sep=_TMUX_FORMAT_SEPARATOR)

                # Create a dict using the fmt_keys as the keys
                _ret.append(dict(zip(self._fmt_keys, line_vals)))
            return _ret

        else:
            raise ValueError("tmux command did not execute correctly; no stdout.")


def tmux_create_detached_window(window_name: str, session_group: str):
    """Create a new detached window

    Args:
        window_name: The name of the window to create.
        session_group: The group of the session to create the window in.

    Raises:
        RuntimeError: If the session group does not exist or the window already exists.
    """

    # Check that session group exists
    if not tmux_session_exists(session_group):
        raise RuntimeError(f"Session group {session_group} does not exist")

    # Check that window doesn't already exist
    if tmux_window_exists(window_name, session_group):
        raise RuntimeError(f"Window {window_name} already exists in session group {session_group}")

    # Create the window
    subprocess.run([tmux_binary, "new-window", "-t", session_group, "-n", window_name, "-d"])


def tmux_create_detached_session(session_group: str, session_name: str = None) -> str:
    """Create a new detached session

    If session_name is not provided, we generate an 8-digit random number to use as the session name, check to make
    sure that it's not already in use, and then create the session.

    Args:
        session_group: The name of the session group in which to create the session.
        session_name (optional): The name of the session to create.

    Returns:
        str: The name of the newly created session.
    """

    if session_name is None:
        session_name = str(random.randint(10000000, 99999999))
        while tmux_session_exists(session_name):
            session_name = str(random.randint(10000000, 99999999))

    subprocess.run([tmux_binary, "new-session", "-s", session_name, "-d", "-t", session_group])
    return session_name


def tmux_attach(session_id: str):
    subprocess.run([tmux_binary, "attach-session", "-t", session_id])


def tmux_attach_window(window_id: str, session_group: str):
    """Attach to a window in a session

    This function will attach to an unattached session in the given session group.
    If no such session exists, it will create a new one and then attach to it.

    Only sessions with names consisting only of numbers are eligible for attachment.

    Args:
        window_id: The ID of the window to attach to.
        session_group: The group of the session to attach to.
    """
    session_to_attach: str = None

    # Do we have any unattached sessions in this group?
    sessions = tmux_list_sessions()
    for session in sessions:
        # Check to see if the session is an attachable session (i.e. has a name consisting only of numbers)
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
        session_to_attach = tmux_create_detached_session(session_group)

    subprocess.run([tmux_binary, "attach-session", "-t", ":".join([session_to_attach, window_id])])


def tmux_list_windows(session_name: str) -> List[Dict[str, str]]:
    """Get a list of all tmux windows sorted by window name.

    Args:
        session_name: The name of the tmux session to list windows for.

    Returns:
        List[Dict[str, str]]: List of dictionaries containing tmux window information.
            Each dictionary contains 'window_id', 'window_name', and 'window_active_clients' keys.
    """
    try:
        tmux_cmd = TmuxFmtCmd(
            ["list-windows", "-t", session_name], ["window_id", "window_name", "window_active_clients"]
        )
    except RuntimeError as e:
        if "no server running" in str(e):
            # This is okay. It just means there's no server yet. We return an empty session
            # list
            return []

    return sorted(tmux_cmd.stdout, key=lambda k: k["window_name"])


def tmux_list_sessions() -> List[Dict[str, str]]:
    """Get a list of all tmux sessions sorted by session name.

    Returns:
        List[Dict[str, str]]: List of dictionaries containing tmux session information.
            Each dictionary contains 'session_id', 'session_name', and 'session_attached' keys.
            Returns an empty list if no tmux server is running.

    Raises:
        RuntimeError: If tmux command fails for any reason other than no server running.
    """
    try:
        tmux_cmd = TmuxFmtCmd(["list-sessions"], ["session_id", "session_name", "session_attached", "session_group"])
    except RuntimeError as e:
        if "no server running" in str(e):
            # This is okay. It just means there's no server yet. We return an empty session
            # list
            return []

    return sorted(tmux_cmd.stdout, key=lambda k: k["session_name"])


def tmux_session_exists(session_name: str) -> bool:
    """Check if a session exists

    Args:
        session_name: The name of the session to check for.

    Returns:
        bool: True if the session exists, False otherwise.
    """
    return session_name in [s["session_name"] for s in tmux_list_sessions()]


def tmux_window_exists(window_name: str, session_group: str) -> bool:
    """Check if a window exists

    Args:
        window_name: The name of the window to check for.
        session_group: The group of the session to check the window in.

    Returns:
        bool: True if the window exists, False otherwise.
    """
    return window_name in [w["window_name"] for w in tmux_list_windows(session_group)]
