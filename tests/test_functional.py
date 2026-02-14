"""Functional tests exercising multiple functions together.

Tmux subprocess layer mocked, everything above it is real.
"""

from unittest.mock import MagicMock, patch

from scry.scry import (
    WINDOW_HISTORY,
    dump_windows_to_yaml,
    load_windows_from_yaml,
    process_command,
    update_window_history,
)


@patch("scry.scry.tmux_list_windows")
@patch("scry.scry.tmux_create_detached_window")
def test_new_window_and_history(mock_create, mock_list, clean_window_history):
    """Create window via process_command, update history, verify history state."""
    mock_list.return_value = [{"window_id": "@10", "window_name": "fresh"}]
    console = MagicMock()

    result, err = process_command("n fresh", [], "main", console)
    assert result == "@10"
    assert err == ""

    update_window_history(result)
    assert WINDOW_HISTORY[-1] == "@10"


@patch("scry.scry.tmux_rename_window")
@patch("scry.scry.tmux_window_exists", return_value=False)
def test_rename_flow(mock_exists, mock_rename, mock_windows, clean_window_history):
    """Select window by index, rename it, verify correct tmux call."""
    console = MagicMock()

    # First select a window by index
    result, err = process_command("0", mock_windows, "main", console)
    assert result == "@1"

    # Now rename it
    result, err = process_command("r 0 renamed", mock_windows, "main", console)
    assert result is None
    assert err == ""
    mock_rename.assert_called_once_with("@1", "renamed", "main")


def test_swap_after_multiple_attaches(clean_window_history):
    """Attach @1, @2, @3 via history; swap returns @2."""
    update_window_history("@1")
    update_window_history("@2")
    update_window_history("@3")

    console = MagicMock()
    windows = []  # Not needed for swap
    result, err = process_command("s", windows, "main", console)
    assert result == "@2"


def test_numeric_selection(mock_windows, clean_window_history):
    """process_command('2', windows, ...) returns correct window."""
    console = MagicMock()
    result, err = process_command("2", mock_windows, "main", console)
    assert result == mock_windows[2]["window_id"]
    assert err == ""


@patch("scry.scry.tmux_create_detached_window")
@patch("scry.scry.tmux_list_windows")
def test_dump_and_load_roundtrip(mock_list, mock_create, tmp_path):
    """Dump windows to real temp file, load from same file, verify only missing windows created."""
    dump_file = str(tmp_path / "roundtrip.yml")
    windows = [
        {"window_id": "@1", "window_name": "alpha"},
        {"window_id": "@2", "window_name": "beta"},
        {"window_id": "@3", "window_name": "gamma"},
    ]

    # Dump
    dump_windows_to_yaml(windows, dump_file)

    # Load — simulate that "beta" already exists
    mock_list.return_value = [{"window_name": "beta"}]
    load_windows_from_yaml(dump_file, "main")

    # Only alpha and gamma should be created
    assert mock_create.call_count == 2
    created_names = [call.args[0] for call in mock_create.call_args_list]
    assert "alpha" in created_names
    assert "gamma" in created_names
    assert "beta" not in created_names
