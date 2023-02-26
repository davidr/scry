#!/usr/bin/env python3

import logging
import re
import sys
from collections import deque
from shutil import get_terminal_size
from time import sleep
from typing import Dict, List, Tuple

from rich import print
from rich.console import Console
from rich.prompt import Prompt

from scry.tmuxcmd import TmuxFmtCmd, tmux_attach, tmux_create_detached

# import readline

logging.basicConfig(
    format="%(asctime)s %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)

_LOGGER = logging.getLogger("")

OPTION_HELP = {
    "##": "Session ID (numerical)",
    "n": "New session (: n sess_name)",
    "q": "Quit",
    "s": "Swap (attach second most recent session)",
    "u": "Update screen",
    "?": "Help",
}


SESSION_HISTORY: deque = deque()
DEBUG = False


config = {
    "minnamelen": 15,
    "n_cols": 5,
    "fmt_overhead": 6,
}


def do_table_loop():
    console = Console()
    display_error_message = ""

    while True:
        # Clear some loop variables
        session_to_attach: str = None

        sessions = tmux_list_sessions()

        # Check to see if our previous session still exits. If not, we'll need to remove it from the history
        if len(SESSION_HISTORY) > 0:
            previous_session = SESSION_HISTORY[-1]
            if not next(
                (session for session in sessions if session["session_id"] == previous_session),
                None,
            ):
                SESSION_HISTORY.pop()

        console.clear()
        lines_printed = draw_table(console, sessions)
        console.line(console.size.height - lines_printed - 2)

        if display_error_message:
            console.print(f"Error: {display_error_message}")
            display_error_message = ""
        else:
            console.line()

        short_options = "/".join(OPTION_HELP.keys())
        command = Prompt.ask(f"Attach [bold magenta]\[{short_options}][/]")

        if command == "":
            # If we have a session history, just attach the most recent one. If not, noop.
            if len(SESSION_HISTORY) > 0:
                tmux_attach(SESSION_HISTORY[-1])
            else:
                continue

        elif command == "s":
            if len(SESSION_HISTORY) > 1:
                session_to_attach = SESSION_HISTORY[-2]
            else:
                continue

        elif command.startswith("n"):
            session_name = command.split()[1]

            if not validate_session_name(session_name):
                _print_err("Invalid session name!")
                continue

            try:
                tmux_create_detached(session_name)
            except RuntimeError as e:
                if "bad session name" in str(e):
                    display_error_message = "Invalid tmux session name"
                    continue

            sessions = tmux_list_sessions()
            session_to_attach = next(
                (session["session_id"] for session in sessions if session["session_name"] == session_name),
                None,
            )

        elif command.isdecimal():
            # We know we have an index number. Find the session and attach it
            session_idx = int(command)

            try:
                session_to_attach = sessions[session_idx]["session_id"]
            except IndexError:
                display_error_message = "Invalid index"
                continue

        elif command == "q":
            sys.exit(0)

        elif command == "?":
            for cmd, help in OPTION_HELP.items():
                console.print(f"\t\t{cmd}\t{help}")
            console.line(2)
            _ = console.input("\[Enter to continue]")

        elif command == "u":
            continue

        else:
            display_error_message = f'command "{command}" not recognized'

        if session_to_attach:
            # If we're just reattaching the same one we were just in, don't alter the history
            if len(SESSION_HISTORY) == 0:
                SESSION_HISTORY.append(session_to_attach)
            elif session_to_attach != SESSION_HISTORY[-1]:
                # If we have it somewhere else in the history, just remove it.
                if session_to_attach in SESSION_HISTORY:
                    SESSION_HISTORY.remove(session_to_attach)
                SESSION_HISTORY.append(session_to_attach)

            tmux_attach(session_to_attach)


def _print_err(err: str) -> None:
    print(f"Error: {err}")
    sleep(0.5)


def format_session_name(name: str, maxlen: int) -> str:
    """Format the tmux session_name, removing middle chars if it is too long

    Args:
        name: session name
        maxlen: maximum size of string to return

    Returns:
        str: formatted sessions name

    """
    if len(name) <= maxlen:
        return name

    # Our name is too long. Trim some chars in the middle and replace with '*'
    startchars = maxlen // 2
    new_name = name[:startchars] + "*" + name[-(maxlen - startchars - 1):]
    return new_name


def validate_session_name(s: str) -> bool:
    session_name_regex = r"^[\w+]+$"
    if re.match(session_name_regex, s):
        return True
    else:
        return False


def draw_table(console: Console, sessions: List[Dict[str, str]]) -> int:
    lines_printed = 0

    console.clear()
    console.rule(f"[bold]scry {len(sessions)}")
    console.line()
    lines_printed += 2

    if DEBUG:
        console.print(console.size)
        console.print(f"Deque: {SESSION_HISTORY}")
        console.print("")
        lines_printed += 3

    if len(sessions) == 0:
        return lines_printed

    n_cols, column_width = get_column_width()
    items_per_col = (len(sessions) + n_cols - 1) // n_cols

    session_strings = format_session_strings(column_width, sessions)

    for i in range(items_per_col):
        for j in range(n_cols):
            index = j * items_per_col + i

            # Does this index exist, or have we run out of sessions before filling the last
            # row?
            if index >= len(session_strings):
                break
            console.print(session_strings[index], end="")

        # print the newline since we're at the end of a row
        console.print("")
        lines_printed += 1

    return lines_printed


def format_session_strings(column_width: int, sessions: List[Dict[str, str]]) -> List[str]:
    session_strings: List[str] = []

    # How many characters do we need for the index numbers?
    n_sessions = len(sessions)
    if n_sessions > 1000:
        # srsly?
        raise RuntimeError(f"you have {n_sessions} sessions, which is too many")
    elif n_sessions > 100:
        idx_len = 3
    elif n_sessions > 10:
        idx_len = 2
    else:
        idx_len = 1

    # Get the max number of chars required to display all session ids
    session_id_len = max(len(x["session_id"]) for x in sessions)

    fmt_overhead = config["fmt_overhead"]
    fmt_overhead += idx_len + session_id_len

    for i, session in enumerate(sessions):
        session_string = ""

        if len(SESSION_HISTORY) > 0:
            # We have at least one session in our history, the most recent. Highlight it
            if session["session_id"] == SESSION_HISTORY[-1]:
                session_string = "[bold reverse magenta]"

        if len(SESSION_HISTORY) > 1:
            if session["session_id"] == SESSION_HISTORY[-2]:
                session_string = "[bold italic green]"

        if len(SESSION_HISTORY) > 2:
            if session["session_id"] == SESSION_HISTORY[-3]:
                session_string = "[bold italic blue]"

        session_string += f"{i:>{idx_len}d})"
        # If the session is attached anywhere, we want to put a hash in the list next to the name
        if session["session_attached"] == "1":
            session_string += "[bold italic]#"
        else:
            session_string += " "

        # The name we use in the display may not be the actual session name, but instead may be
        # a shortened version, returned from format_session_name()
        session_fmt_name = format_session_name(session["session_name"], config["minnamelen"])

        session_string += session_fmt_name

        session_string += " " + "-" * (column_width - len(session_fmt_name) - fmt_overhead)
        session_string += f'[{session["session_id"]:<{session_id_len}}] '
        session_strings.append(session_string)

    return session_strings


def get_column_width() -> Tuple[int, int]:
    # A relatively dirty hack to figure out how many columns we can display
    terminal_size = get_terminal_size()
    n_cols = config["n_cols"] + 1
    column_width: int = 0

    while column_width < (config["fmt_overhead"] + config["minnamelen"] + 3):
        n_cols -= 1
        column_width = (terminal_size.columns - n_cols + 1) // n_cols
        _LOGGER.debug(f"shrinking n_cols to {n_cols}")

    return n_cols, column_width


def tmux_list_sessions() -> List[Dict[str, str]]:
    try:
        tmux_cmd = TmuxFmtCmd(["list-sessions"], ["session_id", "session_name", "session_attached"])
    except RuntimeError as e:
        if "no server running" in str(e):
            # This is okay. It just means there's no server yet. We return an empty session
            # list
            return []

    return sorted(tmux_cmd.stdout, key=lambda k: k["session_name"])
