"""Tests for dump_windows_to_yaml() and load_windows_from_yaml()."""

import json
from unittest.mock import MagicMock, patch

import yaml

from scry.backends.zellij import ZellijBackend
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


@patch("scry.scry.backend.create_detached_window")
@patch("scry.scry.backend.list_windows")
def test_load_creates_missing_windows(mock_list, mock_create, tmp_path):
    """Windows in file but not in tmux get created."""
    dump_file = tmp_path / "windows.yml"
    dump_file.write_text("active_windows:\n  - alpha\n  - beta\n")
    mock_list.return_value = [{"window_name": "alpha"}]
    load_windows_from_yaml(str(dump_file), "main")
    # Only "beta" should be created (alpha already exists)
    mock_create.assert_called_once_with("beta", "main")


@patch("scry.scry.backend.create_detached_window")
@patch("scry.scry.backend.list_windows")
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


def test_load_uses_single_headless_client(tmp_path):
    """Under zellij with zero clients, loading 3 windows spawns one helper client total."""
    dump_file = tmp_path / "windows.yml"
    dump_file.write_text("active_windows:\n  - alpha\n  - beta\n  - gamma\n")
    tabs = [{"tab_id": 0, "name": "Tab #1", "other_focused_clients": []}]
    helper = {"attached": False}

    def fake_run(argv, **kwargs):
        args = argv[1:]
        out = b""
        if args[0] == "list-sessions":
            out = b"main [Created 1m ago]\n"
        elif args[-2:] == ["list-tabs", "-j"]:
            out = json.dumps(tabs).encode()
        elif args[-1] == "list-clients":
            out = b"CLIENT_ID ZELLIJ_PANE_ID RUNNING_COMMAND\n"
            if helper["attached"]:
                out += b"9 terminal_0 bash\n"
        elif "new-tab" in args:
            tabs.append({"tab_id": len(tabs), "name": args[-1], "other_focused_clients": []})
            out = f"{len(tabs) - 1}\n".encode()
        return MagicMock(returncode=0, stdout=out, stderr=b"")

    def fake_pty(*args, **kwargs):
        helper["attached"] = True
        return MagicMock()

    with patch("scry.backends.zellij.find_bin_in_path", return_value="/home/user/bin/zellij"):
        zellij = ZellijBackend()
    with (
        patch("scry.scry.backend", zellij),
        patch("scry.backends.zellij.subprocess.run", side_effect=fake_run) as mock_run,
        patch("scry.backends.zellij.PtyClient", side_effect=fake_pty) as mock_pty,
    ):
        load_windows_from_yaml(str(dump_file), "main")
    assert mock_pty.call_count == 1
    new_tabs = [c.args[0] for c in mock_run.call_args_list if "new-tab" in c.args[0]]
    assert len(new_tabs) == 3
    assert [t["name"] for t in tabs] == ["Tab #1", "alpha", "beta", "gamma"]


@patch("scry.scry.backend.creating_windows")
@patch("scry.scry.backend.list_windows")
def test_load_logs_batch_setup_failure(mock_list, mock_creating, tmp_path):
    """A failure setting up the creation batch (e.g. zellij helper) is logged, not raised."""
    dump_file = tmp_path / "windows.yml"
    dump_file.write_text("active_windows:\n  - alpha\n")
    mock_list.return_value = []
    mock_creating.return_value.__enter__.side_effect = RuntimeError("zellij helper client did not attach")
    # Should not raise
    load_windows_from_yaml(str(dump_file), "main")
