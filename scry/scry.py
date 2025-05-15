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

from scry.tmuxcmd import (
    tmux_attach_window,
    tmux_create_detached_session,
    tmux_create_detached_window,
    tmux_window_exists,
    tmux_list_sessions,
    tmux_list_windows,
)

# import readline

DEBUG = True

# Configure logging to only write to file
_LOGGER = logging.getLogger("")
_LOGGER.setLevel(logging.DEBUG)

# Remove any existing handlers (like the default console handler)
for handler in _LOGGER.handlers[:]:
    _LOGGER.removeHandler(handler)

if DEBUG:
    # Add file handler for /tmp/scry.log
    file_handler = logging.FileHandler("/tmp/scry.log")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
    file_handler.setLevel(logging.DEBUG)
    _LOGGER.addHandler(file_handler)
    _LOGGER.info("\n\n")

OPTION_HELP = {
    "##": "Session ID (numerical)",
    "n": "New session (: n sess_name)",
    "q": "Quit",
    "s": "Swap (attach second most recent session)",
    "u": "Update screen",
    "?": "Help",
}

SESSION_HISTORY: deque = deque()
WINDOW_HISTORY: deque = deque()

config = {
    "minnamelen": 15,
    "n_cols": 4,
    "fmt_overhead": 6,
    "session_group": "main",
}


def do_table_loop():
    """Main interactive loop for the scry tmux session manager.

    This function provides an interactive interface for managing tmux windows.
    It displays a table of available windows and handles user commands for:
    - Attaching to windows
    - Creating new windows
    - Swapping between recent windows
    - Updating the display
    - Showing help information
    - Quitting the application

    The function maintains a history of recently accessed windows and provides
    visual indicators for the current and recently used windows.
    """
    console = Console()
    display_error_message = ""

    while True:
        # Clear some loop variables
        window_to_attach: str = None

        windows = tmux_list_windows(config["session_group"])
        _LOGGER.info(f"windows: {windows}")

        # Check to see if our previous window still exits. If not, we'll need to remove it from the history
        if len(WINDOW_HISTORY) > 0:
            previous_window = WINDOW_HISTORY[-1]
            if not next(
                (window for window in windows if window["window_id"] == previous_window),
                None,
            ):
                WINDOW_HISTORY.pop()

        console.clear()
        lines_printed = draw_table_windows(console, windows)
        console.line(console.size.height - lines_printed - 2)

        if display_error_message:
            console.print(f"Error: {display_error_message}")
            display_error_message = ""
        else:
            console.line()

        short_options = "/".join(OPTION_HELP.keys())
        command = Prompt.ask(f"Attach [bold magenta]\[{short_options}][/]")

        if command == "":
            # If we have a window history, just attach the most recent one. If not, noop.
            if len(WINDOW_HISTORY) > 0:
                tmux_attach_window(WINDOW_HISTORY[-1], config["session_group"])
            else:
                continue

        elif command == "s":
            if len(WINDOW_HISTORY) > 1:
                window_to_attach = WINDOW_HISTORY[-2]
            else:
                continue

        elif command.startswith("n"):
            window_name = command.split()[1]

            if not validate_window_name(window_name):
                _print_err("Invalid window name!")
                continue

            try:
                tmux_create_detached_window(window_name, config["session_group"])
            except RuntimeError as e:
                if "bad window name" in str(e):
                    display_error_message = "Invalid tmux window name"
                    continue

            windows = tmux_list_windows(config["session_group"])
            window_to_attach = next(
                (window["window_id"] for window in windows if window["window_name"] == window_name),
                None,
            )

        elif command.isdecimal():
            # We know we have an index number. Find the window and attach it
            window_idx = int(command)

            try:
                window_to_attach = windows[window_idx]["window_id"]
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

        if window_to_attach:
            # If we're just reattaching the same one we were just in, don't alter the history
            if len(WINDOW_HISTORY) == 0:
                WINDOW_HISTORY.append(window_to_attach)
            elif window_to_attach != WINDOW_HISTORY[-1]:
                # If we have it somewhere else in the history, just remove it.
                if window_to_attach in WINDOW_HISTORY:
                    WINDOW_HISTORY.remove(window_to_attach)
                WINDOW_HISTORY.append(window_to_attach)

            tmux_attach_window(window_to_attach, config["session_group"])


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
    new_name = name[:startchars] + "*" + name[-(maxlen - startchars - 1) :]
    return new_name


def validate_window_name(s: str) -> bool:
    """Validate if a string is a valid tmux window name.

    Args:
        s: The string to validate as a potential tmux window name.

    Returns:
        bool: True if the string is a valid window name (contains only word characters),
            False otherwise.
    """
    window_name_regex = r"^[\w+-.]+$"
    if re.match(window_name_regex, s):
        return True
    else:
        return False


def draw_table(console: Console, sessions: List[Dict[str, str]]) -> int:
    """Draw a formatted table of tmux sessions to the console.

    Args:
        console: Rich Console object to write the table to.
        sessions: List of dictionaries containing tmux session information.
            Each dictionary should contain 'session_id' and 'session_name' keys.

    Returns:
        int: Number of lines printed to the console.
    """
    lines_printed = 0

    console.clear()
    console.rule(f"[bold]scry {len(sessions)}")
    console.line()
    lines_printed += 2

    if len(sessions) == 0:
        return lines_printed

    n_cols, column_width = get_column_width()
    items_per_col = (len(sessions) + n_cols - 1) // n_cols
    _LOGGER.info(f"n_cols: {n_cols}, column_width: {column_width}, items_per_col: {items_per_col}")

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


def draw_table_windows(console: Console, windows: List[Dict[str, str]]) -> int:
    """Draw a formatted table of tmux sessions to the console.

    Args:
        console: Rich Console object to write the table to.
        sessions: List of dictionaries containing tmux session information.
            Each dictionary should contain 'session_id' and 'session_name' keys.

    Returns:
        int: Number of lines printed to the console.
    """
    lines_printed = 0

    console.clear()
    console.rule(f"[bold]scry {len(windows)}")
    console.line()
    lines_printed += 2

    if len(windows) == 0:
        return lines_printed

    n_cols, column_width = get_column_width()
    items_per_col = (len(windows) + n_cols - 1) // n_cols
    _LOGGER.info(f"n_cols: {n_cols}, column_width: {column_width}, items_per_col: {items_per_col}")

    window_strings = format_window_strings(column_width, windows)

    for i in range(items_per_col):
        for j in range(n_cols):
            index = j * items_per_col + i

            # Does this index exist, or have we run out of sessions before filling the last
            # row?
            if index >= len(window_strings):
                break
            console.print(window_strings[index], end="")

        # print the newline since we're at the end of a row
        console.print("")
        lines_printed += 1

    return lines_printed


def format_session_strings(column_width: int, sessions: List[Dict[str, str]]) -> List[str]:
    """Format tmux sessions into display strings with proper formatting and highlighting.

    Args:
        column_width: Width of each column in characters.
        sessions: List of dictionaries containing tmux session information.
            Each dictionary should contain 'session_id', 'session_name', and 'session_attached' keys.

    Returns:
        List[str]: List of formatted strings, each representing a session with proper
            formatting, highlighting, and padding based on the session's state and history.

    Raises:
        RuntimeError: If there are more than 1000 sessions.
    """
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

        if len(WINDOW_HISTORY) > 0:
            # We have at least one session in our history, the most recent. Highlight it
            if session["session_id"] == WINDOW_HISTORY[-1]:
                session_string = "[bold reverse magenta]"

        if len(WINDOW_HISTORY) > 1:
            if session["session_id"] == WINDOW_HISTORY[-2]:
                session_string = "[bold italic green]"

        if len(WINDOW_HISTORY) > 2:
            if session["session_id"] == WINDOW_HISTORY[-3]:
                session_string = "[bold italic blue]"

        session_string += f"{i:>{idx_len}d})"
        # If the session is attached anywhere, we want to put a hash in the list next to the name
        if session["session_attached"] == "1":
            session_string += "[bold italic]#"
        else:
            session_string += " "

        # The name we use in the display may not be the actual session name, but instead may be
        # a shortened version, returned from format_session_name()
        # session_fmt_name = format_session_name(session["session_name"], config["minnamelen"])
        session_fmt_name = format_session_name(session["session_name"], column_width - fmt_overhead)

        session_string += session_fmt_name

        _LOGGER.debug(f"pre-format session_string:  {session_string}, len: {len(session_string)}")

        session_string += " " + "-" * (column_width - len(session_fmt_name) - fmt_overhead)
        session_string += f'[{session["session_id"].replace('@', '$'):<{session_id_len}}] '
        session_strings.append(session_string)

        _LOGGER.debug(f"post-format session_string: {session_string}, len: {len(session_string)}")

    return session_strings


def format_window_strings(column_width: int, windows: List[Dict[str, str]]) -> List[str]:
    """Format tmux windows into display strings with proper formatting and highlighting.

    Args:
        column_width: Width of each column in characters.
        windows: List of dictionaries containing tmux window information.
            Each dictionary should contain 'window_id', 'window_name', and 'window_active_clients' keys.

    Returns:
        List[str]: List of formatted strings, each representing a window with proper
            formatting, highlighting, and padding based on the window's state and history.

    Raises:
        RuntimeError: If there are more than 1000 windows.
    """
    window_strings: List[str] = []

    # How many characters do we need for the index numbers?
    n_windows = len(windows)
    if n_windows > 1000:
        # srsly?
        raise RuntimeError(f"you have {n_windows} windows, which is too many")
    elif n_windows > 100:
        idx_len = 3
    elif n_windows > 10:
        idx_len = 2
    else:
        idx_len = 1

    # Get the max number of chars required to display all session ids
    window_id_len = max(len(x["window_id"]) for x in windows)

    fmt_overhead = config["fmt_overhead"]
    # fmt_overhead += idx_len + window_id_len
    fmt_overhead += idx_len

    for i, window in enumerate(windows):
        window_string = ""

        if len(WINDOW_HISTORY) > 0:
            # We have at least one window in our history, the most recent. Highlight it
            if window["window_id"] == WINDOW_HISTORY[-1]:
                window_string = "[bold reverse magenta]"

        if len(WINDOW_HISTORY) > 1:
            if window["window_id"] == WINDOW_HISTORY[-2]:
                window_string = "[bold italic green]"

        if len(WINDOW_HISTORY) > 2:
            if window["window_id"] == WINDOW_HISTORY[-3]:
                window_string = "[bold italic blue]"

        window_string += f"{i:>{idx_len}d})"
        # If the window is attached anywhere, we want to put a hash in the list next to the name
        if window["window_active_clients"] != "0":
            window_string += "[bold italic]#"
        else:
            window_string += " "

        # The name we use in the display may not be the actual window name, but instead may be
        # a shortened version, returned from format_window_name()
        # window_fmt_name = format_window_name(window["window_name"], config["minnamelen"])
        window_fmt_name = format_session_name(window["window_name"], column_width - fmt_overhead)

        window_string += window_fmt_name

        _LOGGER.debug(f"pre-format window_string:  {window_string}, len: {len(window_string)}")

        window_string += " " + "-" * (column_width - len(window_fmt_name) - fmt_overhead) + " "

        # Replace the @ with $ in the window id to make it
        # window_id_str = window["window_id"].replace('@', '$')
        # window_string += f'[{window_id_str:<{window_id_len}}] '
        window_strings.append(window_string)

        _LOGGER.debug(f"post-format window_string: {window_string}, len: {len(window_string)}")

    return window_strings


def get_column_width() -> Tuple[int, int]:
    """Calculate the number of columns and width of each column for the session display.

    This function determines the optimal number of columns and their width based on
    the terminal size and minimum required width for session display. It will reduce
    the number of columns if necessary to ensure each column has sufficient width.

    Returns:
        Tuple[int, int]: A tuple containing:
            - Number of columns to use for display
            - Width of each column in characters
    """
    # A relatively dirty hack to figure out how many columns we can display
    terminal_size = get_terminal_size()
    n_cols = config["n_cols"] + 1
    column_width: int = 0

    while column_width < (config["fmt_overhead"] + config["minnamelen"] + 3):
        n_cols -= 1
        column_width = (terminal_size.columns - n_cols + 1) // n_cols
        _LOGGER.debug(f"shrinking n_cols to {n_cols}")

    return n_cols, column_width
