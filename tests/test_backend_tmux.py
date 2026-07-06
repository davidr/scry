"""Tests for the tmux backend."""

from unittest.mock import MagicMock, patch

import pytest

from scry.backends.tmux import TmuxBackend, TmuxCmd, TmuxFmtCmd


@pytest.fixture
def backend():
    with patch("scry.backends.tmux.find_bin_in_path", return_value="/usr/bin/tmux"):
        return TmuxBackend()


# --- TmuxCmd / TmuxFmtCmd class tests ---


@patch("scry.backends.tmux.subprocess.run")
def test_tmuxcmd_success(mock_run):
    """returncode=0: stdout returns parsed lines."""
    mock_run.return_value = MagicMock(returncode=0, stdout=b"line1\nline2\n", stderr=b"")
    cmd = TmuxCmd("/usr/bin/tmux", ["list-sessions"])
    assert cmd.stdout == ["line1", "line2"]


@patch("scry.backends.tmux.subprocess.run")
def test_tmuxcmd_nonzero_raises(mock_run):
    """returncode!=0: RuntimeError raised with stderr."""
    mock_run.return_value = MagicMock(returncode=1, stdout=b"", stderr=b"error: something went wrong")
    with pytest.raises(RuntimeError, match="nonzero"):
        TmuxCmd("/usr/bin/tmux", ["bad-command"])


@patch("scry.backends.tmux.subprocess.run")
def test_tmuxcmd_uses_given_binary(mock_run):
    """The binary passed to the constructor is what gets executed."""
    mock_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
    TmuxCmd("/opt/other/tmux", ["list-sessions"])
    assert mock_run.call_args[0][0][0] == "/opt/other/tmux"


@patch("scry.backends.tmux.subprocess.run")
def test_tmuxfmtcmd_parses_output(mock_run):
    """Multi-line __X__-separated output parsed into list of dicts."""
    mock_run.return_value = MagicMock(returncode=0, stdout=b"val1__X__val2\nval3__X__val4\n", stderr=b"")
    cmd = TmuxFmtCmd("/usr/bin/tmux", ["list-windows"], ["key1", "key2"])
    result = cmd.stdout
    assert len(result) == 2
    assert result[0] == {"key1": "val1", "key2": "val2"}
    assert result[1] == {"key1": "val3", "key2": "val4"}


@patch("scry.backends.tmux.subprocess.run")
def test_tmuxfmtcmd_single_line(mock_run):
    """Single line parsed correctly."""
    mock_run.return_value = MagicMock(returncode=0, stdout=b"only__X__line\n", stderr=b"")
    cmd = TmuxFmtCmd("/usr/bin/tmux", ["list-windows"], ["a", "b"])
    result = cmd.stdout
    assert len(result) == 1
    assert result[0] == {"a": "only", "b": "line"}


# --- TmuxBackend method tests ---


@patch("scry.backends.tmux.TmuxFmtCmd")
def test_list_windows_returns_sorted(mock_fmt, backend):
    """Windows returned sorted by window_name."""
    mock_fmt.return_value.stdout = [
        {"window_id": "@2", "window_name": "zeta", "window_active_clients": "0"},
        {"window_id": "@1", "window_name": "alpha", "window_active_clients": "0"},
    ]
    result = backend.list_windows("main")
    assert result[0]["window_name"] == "alpha"
    assert result[1]["window_name"] == "zeta"


@patch("scry.backends.tmux.TmuxFmtCmd")
def test_list_windows_no_server(mock_fmt, backend):
    """RuntimeError 'no server running' returns []."""
    mock_fmt.side_effect = RuntimeError("tmux returned nonzero with stderr: b'no server running'")
    assert backend.list_windows("main") == []


@patch("scry.backends.tmux.TmuxFmtCmd")
def test_list_windows_missing_session_returns_empty(mock_fmt, backend):
    """Server running but session group absent: 'can't find session' returns []."""
    mock_fmt.side_effect = RuntimeError('tmux returned nonzero with stderr: b"can\'t find session: main"')
    assert backend.list_windows("main") == []


@patch("scry.backends.tmux.TmuxFmtCmd")
def test_list_windows_reraises_other_errors(mock_fmt, backend):
    """RuntimeErrors that aren't 'no server' propagate instead of UnboundLocalError."""
    mock_fmt.side_effect = RuntimeError("tmux returned nonzero with stderr: b'permission denied'")
    with pytest.raises(RuntimeError, match="permission denied"):
        backend.list_windows("main")


@patch("scry.backends.tmux.TmuxFmtCmd")
def test_list_sessions_returns_sorted(mock_fmt, backend):
    """Sessions returned sorted by session_name."""
    mock_fmt.return_value.stdout = [
        {"session_id": "$1", "session_name": "zebra", "session_attached": "0", "session_group": "main"},
        {"session_id": "$0", "session_name": "aardvark", "session_attached": "1", "session_group": "main"},
    ]
    result = backend.list_sessions()
    assert result[0]["session_name"] == "aardvark"
    assert result[1]["session_name"] == "zebra"


@patch("scry.backends.tmux.TmuxFmtCmd")
def test_list_sessions_no_server(mock_fmt, backend):
    """RuntimeError 'no server running' returns []."""
    mock_fmt.side_effect = RuntimeError("no server running")
    assert backend.list_sessions() == []


@patch("scry.backends.tmux.TmuxFmtCmd")
def test_list_sessions_reraises_other_errors(mock_fmt, backend):
    """RuntimeErrors that aren't 'no server' propagate."""
    mock_fmt.side_effect = RuntimeError("tmux returned nonzero with stderr: b'permission denied'")
    with pytest.raises(RuntimeError, match="permission denied"):
        backend.list_sessions()


def test_session_exists_true_and_false(backend):
    """Correctly reports existence based on session list."""
    with patch.object(backend, "list_sessions", return_value=[{"session_name": "main"}, {"session_name": "other"}]):
        assert backend.session_exists("main") is True
        assert backend.session_exists("nonexistent") is False


def test_window_exists_true_and_false(backend):
    """Correctly reports existence based on window list."""
    with patch.object(backend, "list_windows", return_value=[{"window_name": "alpha"}, {"window_name": "beta"}]):
        assert backend.window_exists("alpha", "main") is True
        assert backend.window_exists("missing", "main") is False


@patch("scry.backends.tmux.TmuxCmd")
def test_create_window_success(mock_cmd, backend):
    """Validates session exists, window doesn't, then creates."""
    with (
        patch.object(backend, "session_exists", return_value=True),
        patch.object(backend, "window_exists", return_value=False),
    ):
        backend.create_detached_window("newwin", "main")
    mock_cmd.assert_called_once()
    args = mock_cmd.call_args[0][1]
    assert "new-window" in args
    assert "newwin" in args


def test_create_window_no_session(backend):
    """Raises RuntimeError when session doesn't exist."""
    with patch.object(backend, "session_exists", return_value=False):
        with pytest.raises(RuntimeError, match="does not exist"):
            backend.create_detached_window("win", "nosuchgroup")


def test_create_window_duplicate(backend):
    """Raises RuntimeError when window already exists."""
    with (
        patch.object(backend, "session_exists", return_value=True),
        patch.object(backend, "window_exists", return_value=True),
    ):
        with pytest.raises(RuntimeError, match="already exists"):
            backend.create_detached_window("existing", "main")


@patch("scry.backends.tmux.TmuxCmd")
def test_rename_window(mock_cmd, backend):
    """Calls tmux with correct rename-window args."""
    backend.rename_window("@5", "newname", "main")
    args = mock_cmd.call_args[0][1]
    assert "rename-window" in args
    assert "newname" in args
    assert "main:@5" in args


@patch("scry.backends.tmux.TmuxCmd")
def test_ensure_session_group_uses_group_as_name(mock_cmd, backend):
    """ensure_session_group creates a session named after the group."""
    with patch.object(backend, "session_exists", return_value=False):
        backend.ensure_session_group("main")
    args = mock_cmd.call_args[0][1]
    assert "new-session" in args
    assert args.count("main") == 2  # -s main -t main


@patch("scry.backends.tmux.TmuxCmd")
def test_ensure_session_group_noop_when_exists(mock_cmd, backend):
    """ensure_session_group does nothing if the session already exists."""
    with patch.object(backend, "session_exists", return_value=True):
        backend.ensure_session_group("main")
    mock_cmd.assert_not_called()


@patch("scry.backends.tmux.TmuxCmd")
def test_create_session_random_name(mock_cmd, backend):
    """Generates 8-digit name when no name provided."""
    with patch.object(backend, "session_exists", return_value=False):
        result = backend._create_detached_session("main")
    assert result.isdigit()
    assert len(result) == 8


@patch("scry.backends.tmux.TmuxCmd")
def test_attach_window_finds_unattached(mock_cmd, backend):
    """Finds unattached 8-digit session in correct group."""
    sessions = [
        {"session_id": "$0", "session_name": "main", "session_attached": "1", "session_group": "main"},
        {"session_id": "$1", "session_name": "12345678", "session_attached": "0", "session_group": "main"},
    ]
    with (
        patch.object(backend, "list_sessions", return_value=sessions),
        patch.object(backend, "_create_detached_session") as mock_create,
    ):
        backend.attach_window("@1", "main")
    mock_create.assert_not_called()
    args = mock_cmd.call_args[0][1]
    assert "attach-session" in args
    assert "$1:@1" in args


@patch("scry.backends.tmux.TmuxCmd")
def test_attach_window_creates_when_none_available(mock_cmd, backend):
    """Creates new session when all are attached."""
    sessions = [
        {"session_id": "$0", "session_name": "main", "session_attached": "1", "session_group": "main"},
        {"session_id": "$1", "session_name": "12345678", "session_attached": "1", "session_group": "main"},
    ]
    with (
        patch.object(backend, "list_sessions", return_value=sessions),
        patch.object(backend, "_create_detached_session", return_value="newses") as mock_create,
    ):
        backend.attach_window("@1", "main")
    mock_create.assert_called_once_with("main")
    args = mock_cmd.call_args[0][1]
    assert "newses:@1" in args
