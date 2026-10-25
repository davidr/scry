"""Live remote tests over ``ssh localhost`` (opt in with SCRY_LIVE_ZELLIJ=1).

The "remote" is this machine: ssh to localhost runs a wrapper script that sets HOME to a temp
directory (so the real ~/.scry.yml is never read) and execs the project's scry. Each test uses a
throwaway session named scry-test-<pid>-<n> with ZELLIJ_CONFIG_DIR pointed at an empty directory
(stock keybindings). Teardown terminates hosted clients, then kills and deletes the session.
"""

import itertools
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from scry.backends.pty_client import PtyClient
from scry.backends.zellij import ZellijBackend
from scry.remote.attach import attach_command
from scry.remote.cache import RemoteTabRef, read_cache
from scry.remote.config import RemoteConfig
from scry.remote.protocol import EXIT_STALE
from scry.remote.refresh import refresh_remotes


def _ssh_localhost_works():
    """Return True if non-interactive ssh to localhost succeeds quickly."""
    if shutil.which("ssh") is None:
        return False
    argv = ["ssh", "-T", "-o", "BatchMode=yes", "localhost", "true"]
    try:
        return subprocess.run(argv, capture_output=True, timeout=10).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


_ENABLED = os.environ.get("SCRY_LIVE_ZELLIJ") == "1" and shutil.which("zellij") is not None

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not _ENABLED, reason="live zellij tests need SCRY_LIVE_ZELLIJ=1 and zellij on PATH"),
    pytest.mark.skipif(_ENABLED and not _ssh_localhost_works(), reason="live remote tests need `ssh localhost`"),
]

_SESSION_COUNTER = itertools.count()
_SCRY_BIN = str(Path(sys.executable).parent / "scry")


def _poll(predicate, timeout=5.0, interval=0.05):
    """Return predicate()'s first truthy value within timeout, else None."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


class RemoteLive(object):
    """A throwaway zellij session reached through a scry wrapper over ssh localhost.

    Attributes:
        backend: A ZellijBackend using the real zellij binary.
        name: The session name (scry-test-<pid>-<n>).
        remote: The RemoteConfig pointing at the wrapper.
        cache_dir: Where refreshes write their cache.
        clients: PtyClients hosted by the test; terminated at teardown.
    """

    def __init__(self, backend, name, remote, cache_dir):
        self.backend = backend
        self.name = name
        self.remote = remote
        self.cache_dir = cache_dir
        self.clients = []

    def create_session(self, *tab_names):
        """Create the session and the named tabs."""
        self.backend.ensure_session_group(self.name)
        for tab in tab_names:
            self.backend.create_detached_window(tab, self.name)

    def destroy_session(self):
        """Kill and delete the session."""
        for action in ("kill-session", "delete-session"):
            subprocess.run([self.backend._bin, action, self.name], capture_output=True, check=False)

    def refresh(self):
        """Refresh the remote; return the prompt message."""
        return refresh_remotes([self.remote], self.cache_dir, 20.0, False)

    def cache(self):
        """Return the remote's cache dict."""
        return read_cache(self.cache_dir, self.remote.host)

    def tab_id(self, name):
        """Return the tab ID (str) of the named tab."""
        return next(str(w["window_id"]) for w in self.backend.list_windows(self.name) if w["window_name"] == name)

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
def rlive(monkeypatch, tmp_path, real_find_bin_in_path):
    """Yield a RemoteLive for a fresh scry-test-<pid>-<n> session; always clean up."""
    config_dir = tmp_path / "zellij-config"
    config_dir.mkdir()
    monkeypatch.setenv("ZELLIJ_CONFIG_DIR", str(config_dir))
    for var in ("ZELLIJ", "ZELLIJ_SESSION_NAME", "ZELLIJ_PANE_ID"):
        monkeypatch.delenv(var, raising=False)
    with patch("scry.backends.zellij.find_bin_in_path", real_find_bin_in_path):
        backend = ZellijBackend()
    name = f"scry-test-{os.getpid()}-{next(_SESSION_COUNTER)}"
    home = tmp_path / "remote-home"
    home.mkdir()
    # Absolute zellij_bin: non-interactive ssh has no ~/bin on PATH, and HOME here is a temp dir
    (home / ".scry.yml").write_text(
        f'backend: zellij\nsession_group: {name}\nzellij_wake_keys: "\\x07\\x07"\n'
        f"zellij_bin: {shutil.which('zellij')}\n"
    )
    wrapper = tmp_path / "scry-remote"
    wrapper.write_text(f'#!/bin/sh\nHOME={home} exec {_SCRY_BIN} "$@"\n')
    wrapper.chmod(0o755)
    remote = RemoteConfig(key="b", label="loop", host="localhost", scry=str(wrapper))
    session = RemoteLive(backend, name, remote, str(tmp_path / "cache"))
    try:
        yield session
    finally:
        for client in session.clients:
            client.terminate()
        session.destroy_session()


def test_live_list_ok(rlive):
    rlive.create_session("alpha", "beta")
    assert rlive.refresh() == ""
    cache = rlive.cache()
    assert {"alpha", "beta"} <= {t["name"] for t in cache["tabs"]}
    assert re.match(r"^\d+:\d+$", cache["identity"])


def test_live_identity_stable_across_lists(rlive):
    rlive.create_session("alpha")
    assert rlive.refresh() == ""
    first = rlive.cache()["identity"]
    assert rlive.refresh() == ""
    assert rlive.cache()["identity"] == first


def test_live_identity_changes_after_restart(rlive):
    rlive.create_session("alpha")
    assert rlive.refresh() == ""
    first = rlive.cache()["identity"]
    rlive.destroy_session()
    rlive.create_session("alpha")
    assert rlive.refresh() == ""
    assert rlive.cache()["identity"] != first


def test_live_no_session(rlive):
    rlive.create_session("alpha")
    rlive.destroy_session()
    rlive.refresh()
    cache = rlive.cache()
    assert cache["last_attempt"]["status"] == "no_session"
    assert cache["tabs"] == []
    listing = subprocess.run([rlive.backend._bin, "list-sessions", "--no-formatting"], capture_output=True, text=True)
    assert rlive.name not in listing.stdout


def test_live_stale_attach_refused(rlive):
    rlive.create_session("alpha")
    argv = attach_command(rlive.remote, RemoteTabRef("localhost", "1:1", "0"))
    argv[argv.index("-t")] = "-T"
    assert subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=30).returncode == EXIT_STALE


def test_live_attach_lands_on_tab(rlive):
    rlive.create_session("alpha", "beta")
    assert rlive.refresh() == ""
    identity = rlive.cache()["identity"]
    beta = rlive.tab_id("beta")
    _, held_id = rlive.host_client()
    assert _poll(lambda: held_id in rlive.backend._client_tabs(rlive.name))
    start = rlive.backend._client_tabs(rlive.name)[held_id]
    argv = attach_command(rlive.remote, RemoteTabRef("localhost", identity, beta))
    argv[argv.index("-t")] = "-tt"
    ssh = PtyClient(argv, 24, 80)
    rlive.clients.append(ssh)
    ssh.start_draining()
    new = _poll(
        lambda: [c for c, t in rlive.backend._client_tabs(rlive.name).items() if c != held_id and str(t) == beta],
        timeout=10.0,
    )
    assert new, rlive.backend._client_tabs(rlive.name)
    ssh.terminate()
    assert _poll(lambda: new[0] not in rlive.backend._client_tabs(rlive.name))
    assert rlive.backend._client_tabs(rlive.name).get(held_id) == start
