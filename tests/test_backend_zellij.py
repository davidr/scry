"""Tests for the zellij backend."""

import json
import threading
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


def _tabs_json(*tabs):
    """Build pretty-printed list-tabs -j output from (tab_id, name, viewers) tuples."""
    objs = [
        {
            "position": i,
            "name": name,
            "active": bool(viewers),
            "other_focused_clients": viewers,
            "tab_id": tab_id,
            "viewport_rows": 49,
            "viewport_columns": 50,
            "is_fullscreen_active": False,
        }
        for i, (tab_id, name, viewers) in enumerate(tabs)
    ]
    return json.dumps(objs, indent=2).encode()


LIST_TABS_JSON = _tabs_json((2, "zeta", [1, 3]), (0, "Tab #1", []), (1, "alpha", []))


@patch("scry.backends.zellij.subprocess.run")
def test_list_windows_uses_tab_ids_and_client_counts(mock_run, backend):
    """Tab IDs are window_ids; client count comes from other_focused_clients; sorted by name."""
    mock_run.return_value = _completed(stdout=LIST_TABS_JSON)
    assert backend.list_windows("main") == [
        {"window_id": "0", "window_name": "Tab #1", "window_active_clients": "0"},
        {"window_id": "1", "window_name": "alpha", "window_active_clients": "0"},
        {"window_id": "2", "window_name": "zeta", "window_active_clients": "2"},
    ]
    argv = mock_run.call_args[0][0]
    assert argv == ["/home/user/bin/zellij", "--session", "main", "action", "list-tabs", "-j"]


@patch("scry.backends.zellij.subprocess.run")
def test_list_windows_duplicate_names_distinct_ids(mock_run, backend):
    mock_run.return_value = _completed(stdout=_tabs_json((4, "dup", []), (7, "dup", [])))
    windows = backend.list_windows("main")
    assert [(w["window_id"], w["window_name"]) for w in windows] == [("4", "dup"), ("7", "dup")]


@patch("scry.backends.zellij.subprocess.run")
def test_list_windows_session_not_found_returns_empty(mock_run, backend):
    """Other sessions exist but this one doesn't: no windows yet, not an error."""
    mock_run.return_value = _completed(
        returncode=1, stderr=b"Session 'main' not found. The following sessions are active:\nwork\n"
    )
    assert backend.list_windows("main") == []


@patch("scry.backends.zellij.subprocess.run")
def test_list_clients_parses_table(mock_run, backend):
    mock_run.return_value = _completed(
        stdout=(
            b"CLIENT_ID ZELLIJ_PANE_ID RUNNING_COMMAND\n"
            b"1         terminal_5     N/A            \n"
            b"2         terminal_3     N/A            \n"
        )
    )
    assert backend._list_clients("main") == ["1", "2"]
    argv = mock_run.call_args[0][0]
    assert argv == ["/home/user/bin/zellij", "--session", "main", "action", "list-clients"]


@patch("scry.backends.zellij.subprocess.run")
def test_list_clients_header_only(mock_run, backend):
    mock_run.return_value = _completed(stdout=b"CLIENT_ID ZELLIJ_PANE_ID RUNNING_COMMAND\n")
    assert backend._list_clients("main") == []


@patch("scry.backends.zellij.subprocess.run")
def test_client_tabs_maps_clients(mock_run, backend):
    mock_run.return_value = _completed(stdout=LIST_TABS_JSON)
    assert backend._client_tabs("main") == {"1": 2, "3": 2}


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
def test_ensure_session_group_noop_when_exists(mock_run, backend):
    """ensure_session_group does nothing if the session already exists."""
    with patch.object(backend, "session_exists", return_value=True):
        backend.ensure_session_group("main")
    mock_run.assert_not_called()


def _tab(tab_id, rows=22, cols=80):
    """One list-tabs -j object for size checks."""
    return {"tab_id": tab_id, "name": f"t{tab_id}", "viewport_rows": rows, "viewport_columns": cols}


@patch("scry.backends.zellij.PtyClient")
@patch("scry.backends.zellij.subprocess.run")
def test_create_window_no_focus_argv(mock_run, mock_pty, backend):
    """With clients attached, no helper is spawned and the tab is created unfocused."""
    mock_run.return_value = _completed(stdout=b"2\n")
    with (
        patch.object(backend, "session_exists", return_value=True),
        patch.object(backend, "window_exists", return_value=False),
        patch.object(backend, "_list_clients", return_value=["1"]),
        patch.object(backend, "_list_tabs", return_value=[_tab(2)]),
    ):
        backend.create_detached_window("newwin", "main")
    mock_pty.assert_not_called()
    argv = mock_run.call_args[0][0]
    assert argv == ["/home/user/bin/zellij", "--session", "main", "action", "new-tab", "--no-focus", "--name", "newwin"]


@patch("scry.backends.zellij.PtyClient")
@patch("scry.backends.zellij.subprocess.run")
def test_create_window_zero_clients_spawns_headless(mock_run, mock_pty, backend):
    """With zero clients, a headless helper attaches for the new-tab and is terminated after."""
    order = MagicMock()
    order.attach_mock(mock_run, "run")
    order.attach_mock(mock_pty.return_value.terminate, "terminate")
    mock_run.return_value = _completed(stdout=b"2\n")
    clients = iter([[], ["9"]])
    with (
        patch.object(backend, "session_exists", return_value=True),
        patch.object(backend, "window_exists", return_value=False),
        patch.object(backend, "_list_clients", side_effect=lambda s: next(clients)),
        patch.object(backend, "_list_tabs", return_value=[_tab(2)]),
    ):
        backend.create_detached_window("newwin", "main")
    mock_pty.assert_called_once_with(
        ["/home/user/bin/zellij", "attach", "main", "options", "--on-force-close", "detach"], 24, 80
    )
    mock_pty.return_value.start_draining.assert_called_once_with()
    names = [c[0] for c in order.mock_calls]
    assert names == ["run", "terminate"]
    assert order.mock_calls[0].args[0][-4:] == ["new-tab", "--no-focus", "--name", "newwin"]


@patch("scry.backends.zellij.PtyClient")
@patch("scry.backends.zellij.subprocess.run")
def test_headless_client_timeout_raises_and_cleans_up(mock_run, mock_pty, backend):
    """A helper that never shows up in list-clients is terminated and the create fails."""
    clock = [0.0]

    def fake_sleep(seconds):
        clock[0] += seconds

    with (
        patch.object(backend, "session_exists", return_value=True),
        patch.object(backend, "window_exists", return_value=False),
        patch.object(backend, "_list_clients", return_value=[]),
        patch("scry.backends.zellij.time.sleep", side_effect=fake_sleep),
        patch("scry.backends.zellij.time.monotonic", side_effect=lambda: clock[0]),
    ):
        with pytest.raises(RuntimeError, match="did not attach"):
            backend.create_detached_window("newwin", "main")
    mock_pty.return_value.terminate.assert_called_once_with()
    mock_run.assert_not_called()


def _new_tab_runs(mock_run):
    """The argv tails of the new-tab and close-tab-by-id actions run, in order."""
    return [c.args[0][4:] for c in mock_run.call_args_list if c.args[0][4] in ("new-tab", "close-tab-by-id")]


@patch("scry.backends.zellij.PtyClient")
@patch("scry.backends.zellij.subprocess.run")
def test_create_window_unsized_tab_is_recreated_under_helper(mock_run, mock_pty, backend):
    """A listed client that is gone by new-tab yields a 0x0 tab: close it, recreate under a helper.

    zellij briefly lists the --create-background client after creating a session,
    so "clients present" can be stale by the time new-tab runs (zellij #5594).
    """
    mock_run.side_effect = [_completed(stdout=b"2\n"), _completed(), _completed(stdout=b"3\n")]
    clients = iter([["1"], [], ["9"]])
    tabs = iter([[_tab(0), _tab(2, 0, 0)], [_tab(0), _tab(3)]])
    with (
        patch.object(backend, "session_exists", return_value=True),
        patch.object(backend, "window_exists", return_value=False),
        patch.object(backend, "_list_clients", side_effect=lambda s: next(clients)),
        patch.object(backend, "_list_tabs", side_effect=lambda s: next(tabs)),
    ):
        backend.create_detached_window("newwin", "main")
    assert _new_tab_runs(mock_run) == [
        ["new-tab", "--no-focus", "--name", "newwin"],
        ["close-tab-by-id", "2"],
        ["new-tab", "--no-focus", "--name", "newwin"],
    ]
    mock_pty.assert_called_once()
    mock_pty.return_value.terminate.assert_called_once_with()


@patch("scry.backends.zellij.PtyClient")
@patch("scry.backends.zellij.subprocess.run")
def test_create_window_helper_spawned_even_if_clients_listed_on_retry(mock_run, mock_pty, backend):
    """The retry spawns its own helper even when list-clients still shows someone."""
    mock_run.side_effect = [_completed(stdout=b"2\n"), _completed(), _completed(stdout=b"3\n")]
    clients = iter([["1"], ["1"], ["1", "9"]])
    tabs = iter([[_tab(2, 0, 0)], [_tab(3)]])
    with (
        patch.object(backend, "session_exists", return_value=True),
        patch.object(backend, "window_exists", return_value=False),
        patch.object(backend, "_list_clients", side_effect=lambda s: next(clients)),
        patch.object(backend, "_list_tabs", side_effect=lambda s: next(tabs)),
    ):
        backend.create_detached_window("newwin", "main")
    mock_pty.assert_called_once()


@patch("scry.backends.zellij.PtyClient")
@patch("scry.backends.zellij.subprocess.run")
def test_create_window_still_unsized_raises_and_leaves_no_tab(mock_run, mock_pty, backend):
    """If the retry is also 0x0, that tab is closed too and the create fails loudly."""
    mock_run.side_effect = [
        _completed(stdout=b"2\n"),
        _completed(),
        _completed(stdout=b"3\n"),
        _completed(),
    ]
    clients = iter([["1"], [], ["9"]])
    tabs = iter([[_tab(2, 0, 0)], [_tab(3, 0, 0)]])
    with (
        patch.object(backend, "session_exists", return_value=True),
        patch.object(backend, "window_exists", return_value=False),
        patch.object(backend, "_list_clients", side_effect=lambda s: next(clients)),
        patch.object(backend, "_list_tabs", side_effect=lambda s: next(tabs)),
    ):
        with pytest.raises(RuntimeError, match="no size"):
            backend.create_detached_window("newwin", "main")
    assert _new_tab_runs(mock_run)[1::2] == [["close-tab-by-id", "2"], ["close-tab-by-id", "3"]]


@patch("scry.backends.zellij.PtyClient")
def test_forced_helper_inside_helperless_batch(mock_pty, backend):
    """A forced helper spawns inside a batch that started with clients listed (and no helper)."""
    clients = iter([["1"], ["1"], ["1", "9"]])
    with (
        patch.object(backend, "_list_clients", side_effect=lambda s: next(clients)),
        patch("scry.backends.zellij.time.sleep"),
    ):
        with backend.creating_windows("main"):
            mock_pty.assert_not_called()
            with backend._headless_client("main", force=True):
                mock_pty.assert_called_once()
    mock_pty.return_value.terminate.assert_called_once_with()


@patch("scry.backends.zellij.PtyClient")
def test_forced_helper_reuses_active_helper(mock_pty, backend):
    """A forced nested use while our helper is attached spawns nothing more."""
    clients = iter([[], ["9"]])
    with (
        patch.object(backend, "_list_clients", side_effect=lambda s: next(clients)),
        patch("scry.backends.zellij.time.sleep"),
    ):
        with backend.creating_windows("main"):
            with backend._headless_client("main", force=True):
                pass
    mock_pty.assert_called_once()


def test_wait_for_new_client_returns_first_unknown(backend):
    """Known client IDs are ignored; the first new one is returned."""
    clients = iter([["1"], ["1", "7"]])
    with (
        patch.object(backend, "_list_clients", side_effect=lambda s: next(clients)),
        patch("scry.backends.zellij.time.sleep"),
    ):
        assert backend._wait_for_new_client("main", {"1"}) == "7"


@patch("scry.backends.zellij.PtyClient")
def test_creating_windows_is_reentrant(mock_pty, backend):
    """Nested headless-client use spawns one helper; no client listing while one is active."""
    clients = iter([[], ["9"]])
    with (
        patch.object(backend, "_list_clients", side_effect=lambda s: next(clients)) as mock_list,
        patch("scry.backends.zellij.time.sleep"),
    ):
        with backend.creating_windows("main"):
            with backend._headless_client("main"):
                pass
            mock_pty.return_value.terminate.assert_not_called()
    mock_pty.assert_called_once()
    assert mock_list.call_count == 2
    mock_pty.return_value.terminate.assert_called_once_with()


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


@patch("scry.backends.zellij.subprocess.run")
def test_rename_window_by_id(mock_run, backend):
    mock_run.side_effect = [_completed(), _completed(stdout=_tabs_json((4, "newname", [])))]
    backend.rename_window("4", "newname", "main")
    first = mock_run.call_args_list[0][0][0]
    assert first == ["/home/user/bin/zellij", "--session", "main", "action", "rename-tab-by-id", "4", "newname"]


@patch("scry.backends.zellij.subprocess.run")
def test_rename_window_unknown_id_raises(mock_run, backend):
    """zellij exits 0 for nonexistent IDs, so the post-check must catch it."""
    mock_run.side_effect = [_completed(), _completed(stdout=_tabs_json((1, "other", [])))]
    with pytest.raises(RuntimeError, match="no window"):
        backend.rename_window("4", "newname", "main")


@patch("scry.backends.zellij.subprocess.run")
def test_rename_window_not_applied_raises(mock_run, backend):
    mock_run.side_effect = [_completed(), _completed(stdout=_tabs_json((4, "oldname", [])))]
    with pytest.raises(RuntimeError, match="did not take effect"):
        backend.rename_window("4", "newname", "main")


GO_TO = ["/home/user/bin/zellij", "--session", "main", "action", "go-to-tab-by-id"]


@patch("scry.backends.zellij.proxy")
@patch("scry.backends.zellij.PtyClient")
@patch("scry.backends.zellij.subprocess.run")
def test_attach_spawns_client_with_detach_on_force_close(mock_run, mock_pty, mock_proxy, backend):
    """The attach runs on a scry-owned pty and is proxied; the director thread is started."""
    mock_run.return_value = _completed(stdout=_tabs_json((0, "x", [1]), (5, "y", [])))
    with (
        patch.object(backend, "_list_clients", return_value=["1", "4"]),
        patch.object(backend, "_direct_client") as mock_direct,
    ):
        backend.attach_window("5", "main")
    argv = mock_pty.call_args[0][0]
    assert argv == ["/home/user/bin/zellij", "attach", "main", "options", "--on-force-close", "detach"]
    assert mock_proxy.call_args[0][0] is mock_pty.return_value
    args = mock_direct.call_args[0]
    # Client 4 is listed but views no tab: it must still count as known.
    assert args[:5] == ("main", 5, mock_pty.return_value, {"1", "4"}, {"1": 0})


@patch("scry.backends.zellij.proxy")
@patch("scry.backends.zellij.PtyClient")
@patch("scry.backends.zellij.subprocess.run")
def test_attach_known_includes_clients_seen_only_in_tabs(mock_run, mock_pty, mock_proxy, backend):
    """A client that appears between the two snapshot calls is known, not mistaken for ours."""
    mock_run.return_value = _completed(stdout=_tabs_json((0, "x", [1, 2]), (5, "y", [])))
    with (
        patch.object(backend, "_list_clients", return_value=["1"]),
        patch.object(backend, "_direct_client") as mock_direct,
    ):
        backend.attach_window("5", "main")
    assert mock_direct.call_args[0][3] == {"1", "2"}


@patch("scry.backends.zellij.proxy")
@patch("scry.backends.zellij.PtyClient")
@patch("scry.backends.zellij.subprocess.run")
def test_attach_missing_tab_raises_before_spawning(mock_run, mock_pty, mock_proxy, backend):
    mock_run.return_value = _completed(stdout=_tabs_json((0, "x", [1])))
    with (
        patch.object(backend, "_list_clients", return_value=["1"]),
        patch.object(backend, "_direct_client") as mock_direct,
    ):
        with pytest.raises(RuntimeError, match="window 5 no longer exists"):
            backend.attach_window("5", "main")
    mock_pty.assert_not_called()
    mock_proxy.assert_not_called()
    mock_direct.assert_not_called()


def test_wait_for_new_client_stops_when_client_dies(backend):
    """An alive() of False ends the wait at once instead of polling to the timeout."""
    with (
        patch.object(backend, "_list_clients", return_value=["1"]) as mock_list,
        patch("scry.backends.zellij.time.sleep") as mock_sleep,
    ):
        assert backend._wait_for_new_client("main", {"1"}, alive=lambda: False) is None
    assert mock_list.call_count <= 1
    mock_sleep.assert_not_called()


def test_direct_client_passes_liveness_to_wait(backend):
    """The director stops waiting for the new client as soon as its process exits."""
    client = MagicMock()
    gate = threading.Event()
    with patch.object(backend, "_wait_for_new_client", return_value=None) as mock_wait:
        backend._direct_client("main", 5, client, {"1"}, {}, gate)
    assert mock_wait.call_args.kwargs["alive"] == client.is_alive
    assert gate.is_set()


def _direct(backend, client, polls, before=None, new_id="3"):
    """Run _direct_client with zellij calls mocked; return (gate, order of writes/runs)."""
    order = MagicMock()
    order.attach_mock(client.write, "write")
    gate = threading.Event()
    with (
        patch.object(backend, "_wait_for_new_client", return_value=new_id),
        patch.object(backend, "_client_tabs", side_effect=polls),
        patch("scry.backends.zellij.subprocess.run", return_value=_completed()) as mock_run,
        patch("scry.backends.zellij.time.sleep"),
    ):
        order.attach_mock(mock_run, "run")
        backend._direct_client("main", 5, client, {"1"}, before or {}, gate)
    return gate, [(c[0], c.args[0]) for c in order.mock_calls]


def test_direct_client_wakes_then_focuses(backend):
    client = MagicMock()
    gate, calls = _direct(backend, client, [{"3": 5}])
    assert calls == [("write", b"\x07"), ("write", b"\x07"), ("run", GO_TO + ["5"])]
    assert gate.is_set()


def test_direct_client_restores_displaced_client(backend):
    """Another client moved instead: put it back, then re-wake and retry."""
    client = MagicMock()
    polls = [{"1": 5, "3": 2}, {"1": 2, "3": 5}]  # 1 was pulled onto the target tab 5
    gate, calls = _direct(backend, client, polls, before={"1": 2})
    assert calls == [
        ("write", b"\x07"),
        ("write", b"\x07"),
        ("run", GO_TO + ["5"]),
        ("run", GO_TO + ["2"]),
        ("write", b"\x07"),
        ("write", b"\x07"),
        ("run", GO_TO + ["5"]),
    ]
    assert gate.is_set()


def test_direct_client_ignores_client_moving_elsewhere(backend):
    """A client that moved to some other tab (its user switched tabs) is not reverted."""
    client = MagicMock()
    polls = [{"1": 7, "3": 2}, {"1": 7, "3": 5}]
    gate, calls = _direct(backend, client, polls, before={"1": 2})
    assert calls == [("write", b"\x07"), ("write", b"\x07"), ("run", GO_TO + ["5"])]
    assert gate.is_set()


def test_direct_client_custom_wake_keys():
    with patch("scry.backends.zellij.find_bin_in_path", return_value="/home/user/bin/zellij"):
        backend = ZellijBackend(wake_keys="\x01\x1b")
    client = MagicMock()
    _, calls = _direct(backend, client, [{"3": 5}])
    assert [c for c in calls if c[0] == "write"] == [("write", b"\x01"), ("write", b"\x1b")]


def test_attach_returns_when_client_never_appears(backend):
    client = MagicMock()
    gate, calls = _direct(backend, client, [], new_id=None)
    assert calls == []
    assert gate.is_set()


def test_direct_client_stops_when_client_closes(backend):
    """A client that exits mid-direction (write raises EBADF) ends direction quietly."""
    client = MagicMock()
    client.write.side_effect = OSError(9, "pty client is closed")
    gate, calls = _direct(backend, client, [])
    assert [c for c in calls if c[0] == "run"] == []
    assert gate.is_set()


def test_direct_client_stops_on_zellij_error(backend):
    """A zellij failure (e.g. session gone) ends direction quietly with the gate set."""
    client = MagicMock()
    gate, _ = _direct(backend, client, RuntimeError("session gone"))
    assert gate.is_set()
