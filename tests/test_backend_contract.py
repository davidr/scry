"""Contract tests: every backend returns the same normalized shapes."""

import json
from unittest.mock import MagicMock, patch

import pytest

from scry.backends.base import MuxBackend
from scry.backends.tmux import TmuxBackend
from scry.backends.zellij import ZellijBackend

WINDOW_KEYS = {"window_id", "window_name", "window_active_clients"}
SESSION_KEYS = {"session_id", "session_name", "session_attached", "session_group"}

# Per-backend canned CLI output producing windows alpha/beta and session main.
CASES = {
    "tmux": {
        "cls": TmuxBackend,
        "module": "scry.backends.tmux",
        "windows_stdout": b"@2__X__beta__X__1\n@1__X__alpha__X__0\n",
        "sessions_stdout": b"$0__X__main__X__1__X__main\n",
        "no_server_stderr": b"no server running on /tmp/tmux-1000/default",
    },
    "zellij": {
        "cls": ZellijBackend,
        "module": "scry.backends.zellij",
        "windows_stdout": json.dumps(
            [
                {"position": 0, "name": "beta", "tab_id": 1, "other_focused_clients": []},
                {"position": 1, "name": "alpha", "tab_id": 0, "other_focused_clients": []},
            ]
        ).encode(),
        "sessions_stdout": b"main [Created 5s ago] \n",
        "no_server_stderr": b"There is no active session!\n",
    },
}


@pytest.fixture(params=sorted(CASES))
def case(request):
    params = CASES[request.param]
    with patch(f"{params['module']}.find_bin_in_path", return_value="/usr/bin/mux"):
        backend = params["cls"]()
    return backend, params


def _completed(returncode=0, stdout=b"", stderr=b""):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


def test_is_muxbackend(case):
    backend, _ = case
    assert isinstance(backend, MuxBackend)


def test_list_windows_shape_and_sorting(case):
    backend, params = case
    with patch(f"{params['module']}.subprocess.run", return_value=_completed(stdout=params["windows_stdout"])):
        windows = backend.list_windows("main")
    assert [w["window_name"] for w in windows] == ["alpha", "beta"]
    for window in windows:
        assert set(window) == WINDOW_KEYS
        assert all(isinstance(v, str) for v in window.values())


def test_list_sessions_shape(case):
    backend, params = case
    with patch(f"{params['module']}.subprocess.run", return_value=_completed(stdout=params["sessions_stdout"])):
        sessions = backend.list_sessions()
    assert [s["session_name"] for s in sessions] == ["main"]
    for session in sessions:
        assert set(session) == SESSION_KEYS
        assert all(isinstance(v, str) for v in session.values())


def test_list_windows_empty_when_no_server(case):
    backend, params = case
    with patch(
        f"{params['module']}.subprocess.run",
        return_value=_completed(returncode=1, stderr=params["no_server_stderr"]),
    ):
        assert backend.list_windows("main") == []


def test_unexpected_failure_raises_runtime_error(case):
    backend, params = case
    with patch(
        f"{params['module']}.subprocess.run",
        return_value=_completed(returncode=1, stderr=b"catastrophic failure"),
    ):
        with pytest.raises(RuntimeError, match="catastrophic failure"):
            backend.list_windows("main")


def test_create_window_rejects_missing_group(case):
    backend, _ = case
    with patch.object(backend, "session_exists", return_value=False):
        with pytest.raises(RuntimeError, match="does not exist"):
            backend.create_detached_window("w", "nogroup")


def test_create_window_rejects_duplicate(case):
    backend, _ = case
    with (
        patch.object(backend, "session_exists", return_value=True),
        patch.object(backend, "window_exists", return_value=True),
    ):
        with pytest.raises(RuntimeError, match="already exists"):
            backend.create_detached_window("w", "main")
