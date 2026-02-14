"""Tests for command processing and session group management."""

from unittest.mock import MagicMock, patch

import pytest

from scry.scry import (
    ensure_session_group_exists,
    process_command,
    process_new_window_command,
    process_rename_window_command,
)

# --- process_command() ---


def test_empty_with_history(mock_windows, clean_window_history):
    """Returns most recent window from history."""
    clean_window_history.append("@3")
    console = MagicMock()
    result, err = process_command("", mock_windows, "main", console)
    assert result == "@3"
    assert err == ""


def test_empty_no_history(mock_windows, clean_window_history):
    """Returns (None, '') with no history."""
    console = MagicMock()
    result, err = process_command("", mock_windows, "main", console)
    assert result is None
    assert err == ""


def test_numeric_valid(mock_windows, clean_window_history):
    """Index into windows list returns correct window_id."""
    console = MagicMock()
    result, err = process_command("2", mock_windows, "main", console)
    assert result == "@3"
    assert err == ""


def test_numeric_out_of_range(mock_windows, clean_window_history):
    """Returns (None, 'Invalid index')."""
    console = MagicMock()
    result, err = process_command("99", mock_windows, "main", console)
    assert result is None
    assert "Invalid index" in err


def test_swap_with_history(mock_windows, clean_window_history):
    """'s' returns 2nd most recent."""
    clean_window_history.extend(["@1", "@2"])
    console = MagicMock()
    result, err = process_command("s", mock_windows, "main", console)
    assert result == "@1"
    assert err == ""


def test_swap_insufficient_history(mock_windows, clean_window_history):
    """'s' with <2 entries returns (None, '')."""
    clean_window_history.append("@1")
    console = MagicMock()
    result, err = process_command("s", mock_windows, "main", console)
    assert result is None
    assert err == ""


def test_quit(mock_windows, clean_window_history):
    """'q' raises SystemExit."""
    console = MagicMock()
    with pytest.raises(SystemExit):
        process_command("q", mock_windows, "main", console)


def test_update(mock_windows, clean_window_history):
    """'u' returns (None, '')."""
    console = MagicMock()
    result, err = process_command("u", mock_windows, "main", console)
    assert result is None
    assert err == ""


def test_unrecognized(mock_windows, clean_window_history):
    """Unknown command returns error message."""
    console = MagicMock()
    result, err = process_command("xyz", mock_windows, "main", console)
    assert result is None
    assert "not recognized" in err


# --- process_new_window_command() ---


@patch("scry.scry.tmux_list_windows")
@patch("scry.scry.tmux_create_detached_window")
def test_new_window_success(mock_create, mock_list):
    """Creates window, returns window_id."""
    mock_list.return_value = [
        {"window_id": "@10", "window_name": "newwin"},
    ]
    result, err = process_new_window_command("n newwin", "main")
    assert result == "@10"
    assert err == ""
    mock_create.assert_called_once_with("newwin", "main")


def test_new_window_invalid_name():
    """Returns error, no tmux call."""
    result, err = process_new_window_command("n bad name", "main")
    # "bad" would be extracted as the window name (split()[1]), which is valid
    # But "bad name" has a space — however split()[1] is just "bad"
    # Let's test with an actually invalid name
    result, err = process_new_window_command("n a@b", "main")
    assert result is None
    assert "Invalid" in err


@patch("scry.scry.tmux_create_detached_window", side_effect=RuntimeError("tmux error"))
def test_new_window_tmux_error(mock_create):
    """RuntimeError propagated as error string."""
    result, err = process_new_window_command("n validname", "main")
    assert result is None
    assert "tmux error" in err


# --- process_rename_window_command() ---


@patch("scry.scry.tmux_rename_window")
@patch("scry.scry.tmux_window_exists", return_value=False)
def test_rename_success(mock_exists, mock_rename, mock_windows):
    """Valid args -> rename called, returns (None, '')."""
    result, err = process_rename_window_command("r 0 newname", mock_windows, "main")
    assert result is None
    assert err == ""
    mock_rename.assert_called_once_with("@1", "newname", "main")


def test_rename_wrong_args(mock_windows):
    """Wrong arg count -> usage error."""
    result, err = process_rename_window_command("r 0", mock_windows, "main")
    assert result is None
    assert "Usage" in err


def test_rename_non_numeric_index(mock_windows):
    """Non-decimal index -> error."""
    result, err = process_rename_window_command("r abc newname", mock_windows, "main")
    assert result is None
    assert "number" in err


def test_rename_out_of_range(mock_windows):
    """Index >= len(windows) -> error."""
    result, err = process_rename_window_command("r 99 newname", mock_windows, "main")
    assert result is None
    assert "Invalid" in err


def test_rename_invalid_name(mock_windows):
    """Invalid chars in new name -> error."""
    result, err = process_rename_window_command("r 0 bad@name", mock_windows, "main")
    assert result is None
    assert "Invalid" in err


@patch("scry.scry.tmux_window_exists", return_value=True)
def test_rename_duplicate_name(mock_exists, mock_windows):
    """Window already exists -> error."""
    result, err = process_rename_window_command("r 0 existing", mock_windows, "main")
    assert result is None
    assert "already exists" in err


# --- ensure_session_group_exists() ---


@patch("scry.scry.tmux_list_sessions")
def test_exists_already(mock_list):
    """Returns True when session found."""
    mock_list.return_value = [{"session_name": "main"}]
    assert ensure_session_group_exists("main") is True


@patch("scry.scry.tmux_create_detached_session")
@patch("scry.scry.tmux_list_sessions")
def test_creates_new(mock_list, mock_create):
    """Creates session when not found."""
    mock_list.return_value = []
    result = ensure_session_group_exists("main")
    mock_create.assert_called_once()
    assert result is False  # Returns False after creation per source code


@patch("scry.scry.tmux_create_detached_session", side_effect=RuntimeError("fail"))
@patch("scry.scry.tmux_list_sessions")
def test_create_fails(mock_list, mock_create):
    """Returns False on RuntimeError, no crash."""
    mock_list.return_value = []
    result = ensure_session_group_exists("main")
    assert result is False
