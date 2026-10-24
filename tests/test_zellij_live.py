"""Live tests against a real zellij (opt in with SCRY_LIVE_ZELLIJ=1).

Each test runs in a throwaway session named scry-test-<pid>-<n>, with
ZELLIJ_CONFIG_DIR pointed at an empty directory so stock keybindings apply.
Teardown terminates every client the test hosted, then kills and deletes the
session. No other session is ever touched.
"""

import itertools
import os
import shutil
import subprocess
import threading
import time
from unittest.mock import patch

import pytest

from scry.backends.pty_client import PtyClient
from scry.backends.zellij import ZellijBackend

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("SCRY_LIVE_ZELLIJ") != "1" or shutil.which("zellij") is None,
        reason="live zellij tests need SCRY_LIVE_ZELLIJ=1 and zellij on PATH",
    ),
]

_SESSION_COUNTER = itertools.count()

_LOCK_TOGGLE_TWICE = b"\x07\x07"
""" bytes: Ctrl-g twice — enters and leaves lock mode in stock zellij; a net no-op
key press that makes the writing client the routing target for remote actions
"""


def _poll(predicate, timeout=5.0, interval=0.05):
    """Return predicate()'s first truthy value within timeout, else None."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


class LiveSession(object):
    """A throwaway zellij session plus the clients a test hosts in it.

    Attributes:
        backend: A ZellijBackend using the real zellij binary.
        name: The session name (scry-test-<pid>-<n>).
        clients: PtyClients hosted by the test; terminated at teardown.
    """

    def __init__(self, backend, name):
        self.backend = backend
        self.name = name
        self.clients = []

    def host_client(self):
        """Attach a draining PtyClient to the session; return (client, client ID)."""
        known = set(self.backend._list_clients(self.name))
        client = PtyClient([self.backend._bin, "attach", self.name, "options", "--on-force-close", "detach"], 24, 80)
        self.clients.append(client)
        client.start_draining()
        new = _poll(lambda: set(self.backend._list_clients(self.name)) - known)
        assert new, "hosted client did not attach"
        return client, new.pop()


@pytest.fixture
def live(monkeypatch, tmp_path, real_find_bin_in_path):
    """Yield a LiveSession for a fresh scry-test-<pid>-<n> session; always clean up."""
    config_dir = tmp_path / "zellij-config"
    config_dir.mkdir()
    monkeypatch.setenv("ZELLIJ_CONFIG_DIR", str(config_dir))
    for var in ("ZELLIJ", "ZELLIJ_SESSION_NAME", "ZELLIJ_PANE_ID"):
        monkeypatch.delenv(var, raising=False)
    with patch("scry.backends.zellij.find_bin_in_path", real_find_bin_in_path):
        backend = ZellijBackend()
    session = LiveSession(backend, f"scry-test-{os.getpid()}-{next(_SESSION_COUNTER)}")
    try:
        yield session
    finally:
        for client in session.clients:
            client.terminate()
        for action in ("kill-session", "delete-session"):
            subprocess.run([backend._bin, action, session.name], capture_output=True, check=False)


def _tabs_including(live, client_id):
    """Return _client_tabs once client_id appears in it, else None."""
    tabs = live.backend._client_tabs(live.name)
    return tabs if client_id in tabs else None


def _names(live):
    return [w["window_name"] for w in live.backend.list_windows(live.name)]


def test_live_tab_created_with_zero_clients_survives_attach(live):
    """zellij #5594: a tab made while nobody is attached must survive the first attach."""
    live.backend.ensure_session_group(live.name)
    live.backend.create_detached_window("x", live.name)
    assert live.backend._list_clients(live.name) == []
    live.host_client()
    time.sleep(1.5)
    assert "x" in _names(live)


def test_live_create_recovers_from_stale_client_listing(live):
    """A client listed at create time but gone by new-tab must not cost the window.

    zellij briefly lists the --create-background client right after creating a
    session; this injects that stale read deterministically.
    """
    live.backend.ensure_session_group(live.name)
    real_list_clients = live.backend._list_clients
    reads = itertools.count()

    def stale_first(session_group):
        return ["stale"] if next(reads) == 0 else real_list_clients(session_group)

    with patch.object(live.backend, "_list_clients", side_effect=stale_first):
        live.backend.create_detached_window("x", live.name)
    sizes = {t["name"]: (t["viewport_rows"], t["viewport_columns"]) for t in live.backend._list_tabs(live.name)}
    assert list(sizes).count("x") == 1
    assert 0 not in sizes["x"]
    live.host_client()
    time.sleep(1.5)
    assert "x" in _names(live)


def test_live_create_moves_no_client(live):
    """Creating a tab leaves every attached client on the tab it was viewing."""
    live.backend.ensure_session_group(live.name)
    client, client_id = live.host_client()
    client.write(_LOCK_TOGGLE_TWICE)
    before = _poll(lambda: _tabs_including(live, client_id))
    assert before
    live.backend.create_detached_window("y", live.name)
    assert _poll(lambda: "y" in _names(live))
    time.sleep(0.5)
    assert live.backend._client_tabs(live.name) == before


def test_live_rename(live):
    """Renaming by tab ID works with zero clients attached."""
    live.backend.ensure_session_group(live.name)
    live.backend.create_detached_window("x", live.name)
    assert live.backend._list_clients(live.name) == []
    window_id = next(w["window_id"] for w in live.backend.list_windows(live.name) if w["window_name"] == "x")
    live.backend.rename_window(window_id, "x2", live.name)
    names = _names(live)
    assert "x2" in names
    assert "x" not in names


def _move(live, client, client_id, tab_id):
    """Make client the routing target, send it to tab_id, and wait until it is there."""
    client.write(_LOCK_TOGGLE_TWICE)
    time.sleep(0.1)
    live.backend._action(live.name, "go-to-tab-by-id", str(tab_id))
    assert _poll(lambda: live.backend._client_tabs(live.name).get(client_id) == tab_id)


def test_live_attach_moves_only_new_client(live):
    """Attaching to tab c lands the new client on c; clients on a and b stay put."""
    live.backend.ensure_session_group(live.name)
    client_a, id_a = live.host_client()
    client_b, id_b = live.host_client()
    for name in ("a", "b", "c"):
        live.backend.create_detached_window(name, live.name)
    tab = {w["window_name"]: int(w["window_id"]) for w in live.backend.list_windows(live.name)}
    _move(live, client_a, id_a, tab["a"])
    _move(live, client_b, id_b, tab["b"])
    client_a.write(_LOCK_TOGGLE_TWICE)  # A is now the routing target: the worst case
    time.sleep(0.1)

    in_r, in_w = os.pipe()
    out_r, out_w = os.pipe()
    drain = threading.Thread(target=lambda: [None for _ in iter(lambda: os.read(out_r, 65536), b"")], daemon=True)
    drain.start()
    attach = threading.Thread(target=live.backend._attach, args=(str(tab["c"]), live.name, in_r, out_w), daemon=True)
    try:
        attach.start()
        new = _poll(lambda: set(live.backend._list_clients(live.name)) - {id_a, id_b}, timeout=3.0)
        assert new
        id_new = new.pop()
        expected = {id_a: tab["a"], id_b: tab["b"], id_new: tab["c"]}
        assert _poll(lambda: live.backend._client_tabs(live.name) == expected, timeout=3.0), live.backend._client_tabs(
            live.name
        )
    finally:
        os.close(in_w)
        attach.join(3.0)
        alive = attach.is_alive()
        for fd in (in_r, out_w):
            os.close(fd)
        drain.join(1.0)
        os.close(out_r)
    assert not alive, "_attach did not return after stdin EOF"
