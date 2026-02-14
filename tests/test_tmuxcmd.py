"""Tests for scry.tmuxcmd subprocess abstraction layer."""

from unittest.mock import MagicMock, patch

import pytest

from scry.tmuxcmd import (
    TmuxCmd,
    TmuxFmtCmd,
    tmux_attach_window,
    tmux_create_detached_session,
    tmux_create_detached_window,
    tmux_list_sessions,
    tmux_list_windows,
    tmux_rename_window,
    tmux_session_exists,
    tmux_window_exists,
)

# --- TmuxCmd / TmuxFmtCmd class tests ---


@patch("scry.tmuxcmd.subprocess.run")
def test_tmuxcmd_success(mock_run):
    """returncode=0: stdout returns parsed lines."""
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=b"line1\nline2\n",
        stderr=b"",
    )
    cmd = TmuxCmd(["list-sessions"])
    assert cmd.stdout == ["line1", "line2"]


@patch("scry.tmuxcmd.subprocess.run")
def test_tmuxcmd_nonzero_raises(mock_run):
    """returncode!=0: RuntimeError raised with stderr."""
    mock_run.return_value = MagicMock(
        returncode=1,
        stdout=b"",
        stderr=b"error: something went wrong",
    )
    with pytest.raises(RuntimeError, match="nonzero"):
        TmuxCmd(["bad-command"])


@patch("scry.tmuxcmd.subprocess.run")
def test_tmuxfmtcmd_parses_output(mock_run):
    """Multi-line __X__-separated output parsed into list of dicts."""
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=b"val1__X__val2\nval3__X__val4\n",
        stderr=b"",
    )
    cmd = TmuxFmtCmd(["list-windows"], ["key1", "key2"])
    result = cmd.stdout
    assert len(result) == 2
    assert result[0] == {"key1": "val1", "key2": "val2"}
    assert result[1] == {"key1": "val3", "key2": "val4"}


@patch("scry.tmuxcmd.subprocess.run")
def test_tmuxfmtcmd_single_line(mock_run):
    """Single line parsed correctly."""
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=b"only__X__line\n",
        stderr=b"",
    )
    cmd = TmuxFmtCmd(["list-windows"], ["a", "b"])
    result = cmd.stdout
    assert len(result) == 1
    assert result[0] == {"a": "only", "b": "line"}


# --- Module-level function tests ---


@patch("scry.tmuxcmd.TmuxFmtCmd")
def test_list_windows_returns_sorted(mock_fmt):
    """Windows returned sorted by window_name."""
    mock_fmt.return_value.stdout = [
        {"window_id": "@2", "window_name": "zeta", "window_active_clients": "0"},
        {"window_id": "@1", "window_name": "alpha", "window_active_clients": "0"},
    ]
    result = tmux_list_windows("main")
    assert result[0]["window_name"] == "alpha"
    assert result[1]["window_name"] == "zeta"


@patch("scry.tmuxcmd.TmuxFmtCmd")
def test_list_windows_no_server(mock_fmt):
    """RuntimeError 'no server running' returns []."""
    mock_fmt.side_effect = RuntimeError("tmux returned nonzero with stderr: b'no server running'")
    result = tmux_list_windows("main")
    assert result == []


@patch("scry.tmuxcmd.TmuxFmtCmd")
def test_list_sessions_returns_sorted(mock_fmt):
    """Sessions returned sorted by session_name."""
    mock_fmt.return_value.stdout = [
        {"session_id": "$1", "session_name": "zebra", "session_attached": "0", "session_group": "main"},
        {"session_id": "$0", "session_name": "aardvark", "session_attached": "1", "session_group": "main"},
    ]
    result = tmux_list_sessions()
    assert result[0]["session_name"] == "aardvark"
    assert result[1]["session_name"] == "zebra"


@patch("scry.tmuxcmd.TmuxFmtCmd")
def test_list_sessions_no_server(mock_fmt):
    """RuntimeError 'no server running' returns []."""
    mock_fmt.side_effect = RuntimeError("no server running")
    result = tmux_list_sessions()
    assert result == []


@patch("scry.tmuxcmd.tmux_list_sessions")
def test_session_exists_true_and_false(mock_list):
    """Correctly reports existence based on session list."""
    mock_list.return_value = [
        {"session_name": "main"},
        {"session_name": "other"},
    ]
    assert tmux_session_exists("main") is True
    assert tmux_session_exists("nonexistent") is False


@patch("scry.tmuxcmd.tmux_list_windows")
def test_window_exists_true_and_false(mock_list):
    """Correctly reports existence based on window list."""
    mock_list.return_value = [
        {"window_name": "alpha"},
        {"window_name": "beta"},
    ]
    assert tmux_window_exists("alpha", "main") is True
    assert tmux_window_exists("missing", "main") is False


@patch("scry.tmuxcmd.TmuxCmd")
@patch("scry.tmuxcmd.tmux_window_exists", return_value=False)
@patch("scry.tmuxcmd.tmux_session_exists", return_value=True)
def test_create_window_success(mock_sess, mock_win, mock_cmd):
    """Validates session exists, window doesn't, then creates."""
    tmux_create_detached_window("newwin", "main")
    mock_cmd.assert_called_once()
    args = mock_cmd.call_args[0][0]
    assert "new-window" in args
    assert "newwin" in args


@patch("scry.tmuxcmd.tmux_session_exists", return_value=False)
def test_create_window_no_session(mock_sess):
    """Raises RuntimeError when session doesn't exist."""
    with pytest.raises(RuntimeError, match="does not exist"):
        tmux_create_detached_window("win", "nosuchgroup")


@patch("scry.tmuxcmd.tmux_window_exists", return_value=True)
@patch("scry.tmuxcmd.tmux_session_exists", return_value=True)
def test_create_window_duplicate(mock_sess, mock_win):
    """Raises RuntimeError when window already exists."""
    with pytest.raises(RuntimeError, match="already exists"):
        tmux_create_detached_window("existing", "main")


@patch("scry.tmuxcmd.TmuxCmd")
def test_rename_window(mock_cmd):
    """Calls tmux with correct rename-window args."""
    tmux_rename_window("@5", "newname", "main")
    args = mock_cmd.call_args[0][0]
    assert "rename-window" in args
    assert "newname" in args
    assert "main:@5" in args


@patch("scry.tmuxcmd.TmuxCmd")
@patch("scry.tmuxcmd.tmux_session_exists", return_value=False)
def test_create_session_with_name(mock_sess, mock_cmd):
    """Uses provided name, calls new-session."""
    result = tmux_create_detached_session("main", session_name="mysession")
    assert result == "mysession"
    args = mock_cmd.call_args[0][0]
    assert "new-session" in args
    assert "mysession" in args


@patch("scry.tmuxcmd.TmuxCmd")
@patch("scry.tmuxcmd.tmux_session_exists", return_value=False)
def test_create_session_random_name(mock_sess, mock_cmd):
    """Generates 8-digit name when no name provided."""
    result = tmux_create_detached_session("main")
    assert result.isdigit()
    assert len(result) == 8


@patch("scry.tmuxcmd.TmuxCmd")
@patch("scry.tmuxcmd.tmux_create_detached_session")
@patch("scry.tmuxcmd.tmux_list_sessions")
def test_attach_window_finds_unattached(mock_list, mock_create, mock_cmd):
    """Finds unattached 8-digit session in correct group."""
    mock_list.return_value = [
        {"session_id": "$0", "session_name": "main", "session_attached": "1", "session_group": "main"},
        {"session_id": "$1", "session_name": "12345678", "session_attached": "0", "session_group": "main"},
    ]
    tmux_attach_window("@1", "main")
    mock_create.assert_not_called()
    # Should use session_id "$1"
    args = mock_cmd.call_args[0][0]
    assert "attach-session" in args
    assert "$1:@1" in args


@patch("scry.tmuxcmd.TmuxCmd")
@patch("scry.tmuxcmd.tmux_create_detached_session", return_value="newses")
@patch("scry.tmuxcmd.tmux_list_sessions")
def test_attach_window_creates_when_none_available(mock_list, mock_create, mock_cmd):
    """Creates new session when all are attached."""
    mock_list.return_value = [
        {"session_id": "$0", "session_name": "main", "session_attached": "1", "session_group": "main"},
        {"session_id": "$1", "session_name": "12345678", "session_attached": "1", "session_group": "main"},
    ]
    tmux_attach_window("@1", "main")
    mock_create.assert_called_once_with("main")
    args = mock_cmd.call_args[0][0]
    assert "newses:@1" in args
