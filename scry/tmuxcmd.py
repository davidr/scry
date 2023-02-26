# -*- coding: utf-8 -*-
"""module for running tmux commands and parsing output thereof.

"""

import logging
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
    def stdout(self) -> List[str]:
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


def tmux_create_detached(session_name: str):
    subprocess.run([tmux_binary, "new-session", "-s", session_name, "-d"])


def tmux_attach(session_id: str):
    subprocess.run([tmux_binary, "attach-session", "-t", session_id])
