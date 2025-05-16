#!/usr/bin/env python3
"""Interactive tmux window manager with session grouping support.

This module provides a terminal user interface for managing tmux windows and sessions.
It allows users to view, create, and switch between tmux windows within a session group,
maintaining a history of recently accessed windows for quick navigation.

Features:
    - Interactive window listing with multi-column display
    - Window creation and attachment
    - Session group management
    - History-based window switching
    - Visual indicators for active and recently used windows
    - Configurable display parameters

Example:
    To start the tmux window manager:
        $ python -m scry

Todo:
    * Add support for window renaming
"""

import logging
import re
import sys
from collections import deque
from shutil import get_terminal_size
from time import sleep
from typing import Dict, List, Tuple

from rich.console import Console
from rich.prompt import Prompt

from scry.tmuxcmd import (
    tmux_attach_window,
    tmux_create_detached_window,
    tmux_create_detached_session,
    tmux_list_sessions,
    tmux_list_windows,
)

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
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(filename)s %(levelname)s: %(message)s"))
    _LOGGER.addHandler(file_handler)

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
    "fmt_overhead": 3,
    "session_group": "main",
}


def update_window_history(window_to_attach: str) -> None:
    """Update the window history with a new window to attach.

    This function manages the window history deque, ensuring proper ordering and
    preventing duplicates when a new window is about to be attached.

    Args:
        window_to_attach: The window ID that is about to be attached.
    """
    if len(WINDOW_HISTORY) == 0:
        WINDOW_HISTORY.append(window_to_attach)
    elif window_to_attach != WINDOW_HISTORY[-1]:
        # If we have it somewhere else in the history, just remove it
        if window_to_attach in WINDOW_HISTORY:
            WINDOW_HISTORY.remove(window_to_attach)
        WINDOW_HISTORY.append(window_to_attach)


def process_new_window_command(command: str, session_group: str) -> Tuple[str, str]:
    """Process the new window command.

    Args:
        command: The command string starting with 'n' followed by the window name.
        session_group: The session group to create the window in.

    Returns:
        Tuple[str, str]: A tuple containing (window_to_attach, error_message).
            If there's an error, window_to_attach will be None.
    """
    window_name = command.split()[1]

    if not validate_window_name(window_name):
        return None, "Invalid window name!"

    try:
        tmux_create_detached_window(window_name, session_group)
    except RuntimeError as e:
        if "bad window name" in str(e):
            return None, "Invalid tmux window name"
        return None, str(e)

    windows = tmux_list_windows(session_group)
    window_to_attach = next(
        (window["window_id"] for window in windows if window["window_name"] == window_name),
        None,
    )
    return window_to_attach, ""


def setup_display(console: Console, windows: List[Dict[str, str]], error_message: str) -> None:
    """Set up the display for the tmux window list.

    Args:
        console: The Rich console object to write to.
        windows: List of window information dictionaries.
        error_message: Error message to display, if any.
    """
    console.clear()
    lines_printed = draw_table_windows(console, windows)
    console.line(console.size.height - lines_printed - 2)

    if error_message:
        console.print(f"Error: {error_message}")
        sleep(0.75)
    else:
        console.line()


def ensure_session_group_exists(session_group: str) -> bool:
    """Ensure the session group exists, creating it if necessary.

    Args:
        session_group: The name of the session group to check/create.

    Returns:
        bool: True if the session group exists or was created, False if creation failed.
    """
    sessions = tmux_list_sessions()
    if not any(session["session_name"] == session_group for session in sessions):
        try:
            tmux_create_detached_session(session_group, session_name=session_group)
            return False
        except RuntimeError:
            # TODO - something more graceful here
            return False
    return True


def process_command(
    command: str, windows: List[Dict[str, str]], session_group: str, console: Console
) -> Tuple[str, str]:
    """Process a user command and determine the appropriate action.

    Args:
        command: The command string entered by the user.
        windows: List of current windows.
        session_group: The session group being managed.
        console: The Rich console object for displaying help information.

    Returns:
        Tuple[str, str]: A tuple containing (window_to_attach, error_message).
            If there's no window to attach, window_to_attach will be None.
    """
    if command == "":
        if len(WINDOW_HISTORY) > 0:
            return WINDOW_HISTORY[-1], ""
        return None, ""

    elif command == "s":
        if len(WINDOW_HISTORY) > 1:
            return WINDOW_HISTORY[-2], ""
        return None, ""

    elif command.startswith("n"):
        return process_new_window_command(command, session_group)

    elif command.isdecimal():
        window_idx = int(command)
        try:
            return windows[window_idx]["window_id"], ""
        except IndexError:
            return None, "Invalid index"

    elif command == "q":
        sys.exit(0)

    elif command == "?":
        for cmd, help_string in OPTION_HELP.items():
            console.print(f"\t\t{cmd}\t{help_string}")
        console.line(2)
        _ = console.input("[Enter to continue]")
        return None, ""

    elif command == "u":
        return None, ""

    return None, f'command "{command}" not recognized'


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
        _LOGGER.debug("Starting loop")
        windows = tmux_list_windows(config["session_group"])
        _LOGGER.info("windows: %s", windows)

        # Ensure session group exists if we have no windows
        if len(windows) == 0 and not ensure_session_group_exists(config["session_group"]):
            display_error_message = "Did not find session group, attempted to create it"
            continue

        # Clean up history if needed
        if len(WINDOW_HISTORY) > 0:
            previous_window = WINDOW_HISTORY[-1]
            if not next(
                (window for window in windows if window["window_id"] == previous_window),
                None,
            ):
                WINDOW_HISTORY.pop()

        # Display current state
        setup_display(console, windows, display_error_message)
        display_error_message = ""

        # Get and process command
        short_options = "/".join(OPTION_HELP.keys())
        command = Prompt.ask(f"Attach [bold magenta]\[{short_options}][/]")

        window_to_attach, display_error_message = process_command(command, windows, config["session_group"], console)

        if window_to_attach:
            update_window_history(window_to_attach)
            tmux_attach_window(window_to_attach, config["session_group"])


def format_session_name(name: str, maxlen: int) -> str:
    """Format the tmux session_name, removing middle chars if it is too long

    Args:
        name: session name
        maxlen: maximum size of string to return

    Returns:
        str: formatted sessions name

    Todo:
        * Only elide letters, not numbers, on the basis that numbers are more important

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

    return False


def draw_table_windows(console: Console, windows: List[Dict[str, str]]) -> int:
    """Draw a formatted table of tmux sessions to the console.

    Args:
        console: Rich Console object to write the table to.
        windows: List of dictionaries containing tmux window information.
            Each dictionary should contain 'window_id' and 'window_name' keys.

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
    _LOGGER.info("n_cols: %s, column_width: %s, items_per_col: %s", n_cols, column_width, items_per_col)

    window_strings = format_window_strings(column_width, windows)

    for i in range(items_per_col):
        for j in range(n_cols):
            index = j * items_per_col + i

            # Does this index exist, or have we run out of sessions before filling the last
            # row?
            if index >= len(window_strings):
                break
            _LOGGER.debug("i: %s, j: %s, index: %s", i, j, index)
            console.print(window_strings[index], end="")

        # print the newline since we're at the end of a row
        console.print("")
        lines_printed += 1

    return lines_printed


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

    fmt_overhead = config["fmt_overhead"]
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
        _LOGGER.debug("pre-format window_string:  <<%s>>, len: %s", window_string, len(window_string))

        window_string += " " + "-" * (column_width - len(window_fmt_name) - fmt_overhead) + " "

        # Replace the @ with $ in the window id to make it
        # window_id_str = window["window_id"].replace('@', '$')
        # window_string += f'[{window_id_str:<{window_id_len}}] '
        window_strings.append(window_string)
        _LOGGER.debug("post-format window_string: <<%s>>, len: %s", window_string, len(window_string))

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
    _LOGGER.debug("terminal_size: %s", terminal_size)
    n_cols = config["n_cols"] + 1
    column_width: int = 0

    while column_width < (config["fmt_overhead"] + config["minnamelen"] + 3):
        _LOGGER.debug(
            "cwidth: %s < fmt_overhead: %s + minnamelen: %s + 3",
            column_width,
            config["fmt_overhead"],
            config["minnamelen"],
        )
        n_cols -= 1
        column_width = (terminal_size.columns - n_cols) // n_cols
        _LOGGER.debug("shrinking n_cols to %s", n_cols)

    return n_cols, column_width
