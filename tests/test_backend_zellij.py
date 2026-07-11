"""Tests for the zellij backend."""

from unittest.mock import MagicMock, patch

import pytest

from scry.backends.zellij import ZellijBackend, ZellijCmd


@pytest.fixture
def backend():
    with patch("scry.backends.zellij.find_bin_in_path", return_value="/home/user/bin/zellij"):
        return ZellijBackend()


def _completed(returncode=0, stdout=b"", stderr=b""):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


# --- ZellijCmd class tests ---


@patch("scry.backends.zellij.subprocess.run")
def test_zellijcmd_success(mock_run):
    """returncode=0: stdout returns parsed lines."""
    mock_run.return_value = _completed(stdout=b"line1\nline2\n")
    cmd = ZellijCmd("/home/user/bin/zellij", ["list-sessions"])
    assert cmd.stdout == ["line1", "line2"]


@patch("scry.backends.zellij.subprocess.run")
def test_zellijcmd_nonzero_raises(mock_run):
    """returncode!=0: RuntimeError raised with stderr."""
    mock_run.return_value = _completed(returncode=1, stderr=b"Some other error")
    with pytest.raises(RuntimeError, match="nonzero"):
        ZellijCmd("/home/user/bin/zellij", ["action", "bad"])


@patch("scry.backends.zellij.subprocess.run")
def test_zellijcmd_no_capture_runs_without_pipes(mock_run):
    """capture=False leaves stdout/stderr connected to the tty (attach needs this)."""
    mock_run.return_value = _completed()
    ZellijCmd("/home/user/bin/zellij", ["attach", "main"], capture=False)
    kwargs = mock_run.call_args[1]
    assert kwargs["stdout"] is None
    assert kwargs["stderr"] is None


# --- ZellijBackend method tests ---


@patch("scry.backends.zellij.subprocess.run")
def test_list_windows_names_are_id_and_name(mock_run, backend):
    """Tab names double as window_id and window_name; active clients always 0; sorted."""
    mock_run.return_value = _completed(stdout=b"zeta\nTab #1\nalpha\n")
    windows = backend.list_windows("main")
    assert windows == [
        {"window_id": "Tab #1", "window_name": "Tab #1", "window_active_clients": "0"},
        {"window_id": "alpha", "window_name": "alpha", "window_active_clients": "0"},
        {"window_id": "zeta", "window_name": "zeta", "window_active_clients": "0"},
    ]
    argv = mock_run.call_args[0][0]
    assert argv == ["/home/user/bin/zellij", "--session", "main", "action", "query-tab-names"]


@patch("scry.backends.zellij.subprocess.run")
def test_list_windows_no_session_returns_empty(mock_run, backend):
    """'There is no active session!' means no windows yet, not an error."""
    mock_run.return_value = _completed(returncode=1, stderr=b"There is no active session!\n")
    assert backend.list_windows("main") == []


@patch("scry.backends.zellij.subprocess.run")
def test_list_windows_reraises_other_errors(mock_run, backend):
    mock_run.return_value = _completed(returncode=1, stderr=b"Permission denied")
    with pytest.raises(RuntimeError, match="Permission denied"):
        backend.list_windows("main")


@patch("scry.backends.zellij.subprocess.run")
def test_list_sessions_parses_and_normalizes(mock_run, backend):
    """First whitespace-separated token is the session name; group mirrors name."""
    mock_run.return_value = _completed(stdout=b"work [Created 2h ago] \nmain [Created 5s ago] \n")
    sessions = backend.list_sessions()
    assert sessions == [
        {"session_id": "main", "session_name": "main", "session_attached": "0", "session_group": "main"},
        {"session_id": "work", "session_name": "work", "session_attached": "0", "session_group": "work"},
    ]
    argv = mock_run.call_args[0][0]
    assert argv == ["/home/user/bin/zellij", "list-sessions", "--no-formatting"]


@patch("scry.backends.zellij.subprocess.run")
def test_list_sessions_skips_exited(mock_run, backend):
    """EXITED (resurrectable) sessions are treated as missing."""
    mock_run.return_value = _completed(
        stdout=b"main [Created 5s ago] \ndead [Created 3d ago] (EXITED - attach to resurrect)\n"
    )
    sessions = backend.list_sessions()
    assert [s["session_name"] for s in sessions] == ["main"]


@patch("scry.backends.zellij.subprocess.run")
def test_list_sessions_none_returns_empty(mock_run, backend):
    """'No active zellij sessions found.' maps to []."""
    mock_run.return_value = _completed(returncode=1, stderr=b"No active zellij sessions found.\n")
    assert backend.list_sessions() == []


@patch("scry.backends.zellij.subprocess.run")
def test_ensure_session_group(mock_run, backend):
    mock_run.return_value = _completed()
    backend.ensure_session_group("main")
    argv = mock_run.call_args[0][0]
    assert argv == ["/home/user/bin/zellij", "attach", "--create-background", "main"]


@patch("scry.backends.zellij.subprocess.run")
def test_create_window_success(mock_run, backend):
    mock_run.return_value = _completed(stdout=b"2\n")
    with (
        patch.object(backend, "session_exists", return_value=True),
        patch.object(backend, "window_exists", return_value=False),
    ):
        backend.create_detached_window("newwin", "main")
    argv = mock_run.call_args[0][0]
    assert argv == ["/home/user/bin/zellij", "--session", "main", "action", "new-tab", "--name", "newwin"]


def test_create_window_no_session(backend):
    with patch.object(backend, "session_exists", return_value=False):
        with pytest.raises(RuntimeError, match="does not exist"):
            backend.create_detached_window("win", "nosuchgroup")


def test_create_window_duplicate(backend):
    """Zellij allows duplicate tab names; scry must not."""
    with (
        patch.object(backend, "session_exists", return_value=True),
        patch.object(backend, "window_exists", return_value=True),
    ):
        with pytest.raises(RuntimeError, match="already exists"):
            backend.create_detached_window("existing", "main")


def test_rename_window_unsupported(backend):
    """Zellij cannot rename a tab remotely; a clear error beats a silent no-op."""
    with pytest.raises(RuntimeError, match="not supported"):
        backend.rename_window("oldname", "newname", "main")


@patch("scry.backends.zellij.threading.Thread")
@patch("scry.backends.zellij.subprocess.run")
def test_attach_window_focuses_attaches_and_spawns_refocus(mock_run, mock_thread, backend):
    """Pre-focus (captured), blocking attach (uncaptured), daemon refocus thread started."""
    mock_run.return_value = _completed()
    backend.attach_window("mytab", "main")
    calls = mock_run.call_args_list
    assert calls[0][0][0] == ["/home/user/bin/zellij", "--session", "main", "action", "go-to-tab-name", "mytab"]
    assert calls[1][0][0] == ["/home/user/bin/zellij", "attach", "main"]
    assert calls[1][1]["stdout"] is None  # attach must own the tty
    assert mock_thread.call_args[1]["daemon"] is True
    mock_thread.return_value.start.assert_called_once()


@patch("scry.backends.zellij.time.sleep")
@patch("scry.backends.zellij.subprocess.run")
def test_refocus_tab_fires_per_delay_and_stops_on_error(mock_run, mock_sleep, backend):
    """One go-to-tab-name per configured delay; first failure ends the loop quietly."""
    mock_run.return_value = _completed()
    backend._refocus_tab("mytab", "main")
    assert mock_run.call_count == 2
    assert all(c[0][0][-2:] == ["go-to-tab-name", "mytab"] for c in mock_run.call_args_list)

    mock_run.reset_mock()
    mock_run.return_value = _completed(returncode=1, stderr=b"session gone")
    backend._refocus_tab("mytab", "main")  # must not raise
    assert mock_run.call_count == 1
