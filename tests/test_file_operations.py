"""Tests for dump_windows_to_yaml() and load_windows_from_yaml()."""

from unittest.mock import patch

import yaml

from scry.scry import dump_windows_to_yaml, load_windows_from_yaml


def test_dump_writes_valid_yaml(tmp_path):
    """Writes file, re-read produces correct {'active_windows': [...]}."""
    dump_file = tmp_path / "windows.yml"
    windows = [
        {"window_name": "alpha", "window_id": "@1"},
        {"window_name": "beta", "window_id": "@2"},
    ]
    dump_windows_to_yaml(windows, str(dump_file))
    with open(dump_file, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert data == {"active_windows": ["alpha", "beta"]}


def test_dump_io_error(tmp_path):
    """Unwritable path doesn't raise, logs error."""
    bad_path = str(tmp_path / "nonexistent_dir" / "file.yml")
    windows = [{"window_name": "alpha", "window_id": "@1"}]
    # Should not raise
    dump_windows_to_yaml(windows, bad_path)


@patch("scry.scry.tmux_create_detached_window")
@patch("scry.scry.tmux_list_windows")
def test_load_creates_missing_windows(mock_list, mock_create, tmp_path):
    """Windows in file but not in tmux get created."""
    dump_file = tmp_path / "windows.yml"
    dump_file.write_text("active_windows:\n  - alpha\n  - beta\n")
    mock_list.return_value = [{"window_name": "alpha"}]
    load_windows_from_yaml(str(dump_file), "main")
    # Only "beta" should be created (alpha already exists)
    mock_create.assert_called_once_with("beta", "main")


@patch("scry.scry.tmux_create_detached_window")
@patch("scry.scry.tmux_list_windows")
def test_load_skips_existing(mock_list, mock_create, tmp_path):
    """Windows already present are not re-created."""
    dump_file = tmp_path / "windows.yml"
    dump_file.write_text("active_windows:\n  - alpha\n  - beta\n")
    mock_list.return_value = [
        {"window_name": "alpha"},
        {"window_name": "beta"},
    ]
    load_windows_from_yaml(str(dump_file), "main")
    mock_create.assert_not_called()


def test_load_file_not_found():
    """Missing file doesn't crash."""
    # Should not raise
    load_windows_from_yaml("/nonexistent/path/windows.yml", "main")


def test_load_invalid_yaml_structure(tmp_path):
    """Missing/wrong-type active_windows key handled."""
    dump_file = tmp_path / "windows.yml"
    # Missing active_windows key
    dump_file.write_text("something_else: true\n")
    load_windows_from_yaml(str(dump_file), "main")

    # Wrong type for active_windows
    dump_file.write_text("active_windows: not_a_list\n")
    load_windows_from_yaml(str(dump_file), "main")
