# -*- coding: utf-8 -*-
"""zellij backend: subprocess command wrapper and the ZellijBackend implementation."""

import json
import logging
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, ContextManager, Dict, Iterator, List, Optional, Set

from scry.backends.base import MuxBackend
from scry.backends.pty_client import PtyClient, proxy
from scry.bin_utils import find_bin_in_path

_NO_SESSION_MARKERS = (
    "There is no active session",
    "No active zellij sessions found",
    "not found. The following sessions are active",
)
""" tuple: stderr fragments that mean "no session/server yet", not a real error
"""

DEFAULT_WAKE_KEYS = "\x07\x07"
""" str: default zellij_wake_keys — Ctrl-g twice (lock, unlock) under zellij's stock
keybindings; a net no-op key press sequence that makes a client the target of
remote actions
"""

_WAKE_KEY_INTERVAL = 0.05
""" float: seconds between wake key presses (each key is a separate write)
"""

_DIRECT_ATTEMPTS = 3
""" int: wake + go-to-tab-by-id attempts before giving up on directing a new client
"""

_DIRECT_CONFIRM_TIMEOUT = 1.0
""" float: seconds to poll list-tabs for the outcome of one direction attempt
"""

_CLIENT_POLL_INTERVAL = 0.05
""" float: seconds between list-clients polls while waiting for a client to attach
"""

_HELPER_ROWS, _HELPER_COLS = 24, 80
""" int, int: pty size of the headless helper client; any nonzero size avoids
zellij's 0x0 tab (zellij-org/zellij#5594)
"""

_LOGGER = logging.getLogger(__name__)


def _map_client_tabs(tabs: List[Dict]) -> Dict[str, int]:
    """Map each client ID (str) in list-tabs -j output to the tab_id it is viewing."""
    mapping: Dict[str, int] = {}
    for tab in tabs:
        for client in tab.get("other_focused_clients", []):
            mapping[str(client)] = tab["tab_id"]
    return mapping


class ZellijCmd(object):
    """Run one zellij command; raise RuntimeError on nonzero exit.

    Attributes:
        _zellij_bin: Path to the zellij binary.
        _zellij_args: List of arguments to pass to zellij.
        _cmd_executed: Boolean indicating if the command has been executed.
        _cmd: CompletedProcess object containing the results of the command execution.
        _cwd: Path object representing the working directory for command execution.
    """

    def __init__(self, zellij_bin: str, cmd_args: List[str], capture: bool = True):
        """Initialize and execute a zellij command.

        Args:
            zellij_bin: Fully qualified path of the zellij binary.
            cmd_args: List of arguments to pass to the zellij binary.
            capture: Pipe stdout/stderr (True) or leave them on the tty
                (False — required for `zellij attach`).
        """
        self._zellij_bin = zellij_bin
        self._zellij_args = cmd_args
        self._capture = capture
        self._cmd_executed: bool = False
        self._cmd: subprocess.CompletedProcess = None
        self._cwd: Path = Path.home()

        self._execute_cmd()

    def _execute_cmd(self) -> None:
        pipe = subprocess.PIPE if self._capture else None
        cmd = subprocess.run(
            [self._zellij_bin] + self._zellij_args,
            stdout=pipe,
            stderr=pipe,
            cwd=self._cwd,
            check=False,
        )

        _LOGGER.debug("executing %s", " ".join([self._zellij_bin] + self._zellij_args))
        _LOGGER.debug("stdout %s", cmd.stdout)

        if cmd.returncode != 0:
            _LOGGER.error("zellij returned nonzero with stderr: %s", cmd.stderr)
            raise RuntimeError(f"zellij returned nonzero with stderr: {cmd.stderr}")
        if cmd.stderr and any(marker.encode() in cmd.stderr for marker in _NO_SESSION_MARKERS):
            # zellij 0.45.1 exits 0 when the --session target does not exist.
            _LOGGER.debug("zellij found no session; stderr: %s", cmd.stderr)
            raise RuntimeError(f"zellij found no session: {cmd.stderr}")

        self._cmd = cmd
        self._cmd_executed = True

    @property
    def stdout(self) -> List:
        """
        Get the stdout of the zellij command.

        Returns:
            List[str]: The stdout of the zellij command.
        """
        if self._cmd_executed and self._cmd.stdout is not None:
            stdout = self._cmd.stdout.decode("utf-8")
            return stdout.splitlines()

        raise ValueError("zellij command did not execute correctly; no stdout.")


class ZellijBackend(MuxBackend):
    """MuxBackend implementation for zellij (0.45.1 or later).

    The session_group maps to a single zellij session; tabs are windows and
    the stable tab ID (as a string) is the window_id, so renames keep a
    window's identity. All clients attach to the same session, relying on
    zellij's multiplayer behavior for independent per-client focus;
    window_active_clients counts the clients viewing each tab.

    zellij cannot target a client with remote actions: they go to the client
    that most recently received key input. Attach therefore runs the new
    client on a scry-owned pty, presses the configured wake keys in it so it
    becomes that target, and only then sends go-to-tab-by-id (see _attach).
    Creating tabs uses new-tab --no-focus, which moves no client.
    """

    def __init__(self, wake_keys: str = DEFAULT_WAKE_KEYS):
        """
        Args:
            wake_keys: Key presses zellij consumes as a net no-op under the
                user's keybindings; each character is written as one key press.
        """
        self._bin = find_bin_in_path("zellij")
        self._headless_depth = 0
        self._helper: Optional[PtyClient] = None
        self._wake_keys = wake_keys

    def _action(self, session_group: str, *action_args: str) -> ZellijCmd:
        return ZellijCmd(self._bin, ["--session", session_group, "action", *action_args])

    def _list_tabs(self, session_group: str) -> List[Dict]:
        """Return the session's tabs as parsed from `action list-tabs -j`.

        Raises:
            RuntimeError: If the command fails (including a missing session).
        """
        cmd = self._action(session_group, "list-tabs", "-j")
        return json.loads("\n".join(cmd.stdout))

    def _list_clients(self, session_group: str) -> List[str]:
        """Return the IDs of clients attached to the session (strings)."""
        cmd = self._action(session_group, "list-clients")
        return [line.split()[0] for line in cmd.stdout[1:] if line.strip()]

    def _client_tabs(self, session_group: str) -> Dict[str, int]:
        """Map each attached client ID (str) to the tab_id it is viewing."""
        return _map_client_tabs(self._list_tabs(session_group))

    def _wait_for_new_client(
        self,
        session_group: str,
        known: Set[str],
        timeout: float = 3.0,
        alive: Optional[Callable[[], bool]] = None,
    ) -> Optional[str]:
        """Poll list-clients until a client not in known appears.

        Args:
            session_group: The zellij session.
            known: Client IDs to ignore (those attached before the new client).
            timeout: Seconds to keep polling.
            alive: If given, polling stops (returning None) as soon as it
                returns False, e.g. the awaited client's process has exited.

        Returns:
            The first new client ID, or None if none appeared within timeout.
        """
        deadline = time.monotonic() + timeout
        while True:
            if alive is not None and not alive():
                return None
            new = [c for c in self._list_clients(session_group) if c not in known]
            if new:
                return new[0]
            if time.monotonic() >= deadline:
                return None
            time.sleep(_CLIENT_POLL_INTERVAL)

    @contextmanager
    def _headless_client(self, session_group: str, force: bool = False) -> Iterator[None]:
        """Try to keep at least one client attached for the duration of the block.

        A tab created while zero clients are attached is 0x0, buffers output
        without bound, and is discarded when the first client attaches
        (zellij-org/zellij#5594). If no client is attached (or force is set), a
        helper client is attached on a scry-owned pty (draining its output) and
        terminated on exit; --on-force-close detach makes that terminate a
        detach, never a session quit. Re-entrant: nested use spawns nothing,
        unless force is set and no helper of ours is attached.

        Without force this is best effort: a listed client may already be
        leaving (zellij briefly lists the --create-background client), so
        create_detached_window verifies the result and retries with force.

        Args:
            session_group: The zellij session.
            force: Spawn a helper even if clients are listed, unless ours is active.

        Raises:
            RuntimeError: If the helper does not appear in list-clients in time.
        """
        helper: Optional[PtyClient] = None
        if self._helper is None and (force or self._headless_depth == 0):
            known = set(self._list_clients(session_group))
            if force or not known:
                helper = PtyClient(
                    [self._bin, "attach", session_group, "options", "--on-force-close", "detach"],
                    _HELPER_ROWS,
                    _HELPER_COLS,
                )
                self._helper = helper
        try:
            if helper is not None:
                helper.start_draining()
                if self._wait_for_new_client(session_group, known) is None:
                    raise RuntimeError("zellij helper client did not attach")
            self._headless_depth += 1
            try:
                yield
            finally:
                self._headless_depth -= 1
        finally:
            if helper is not None:
                self._helper = None
                helper.terminate()

    def creating_windows(self, session_group: str) -> ContextManager[None]:
        """Keep one helper client attached (if none is) across a batch of creates."""
        return self._headless_client(session_group)

    def list_windows(self, session_group: str) -> List[Dict[str, str]]:
        """Return the session's tabs as windows sorted by name; [] if no session."""
        try:
            tabs = self._list_tabs(session_group)
        except RuntimeError as e:
            _LOGGER.error("zellij command failed: %s", str(e))
            if any(marker in str(e) for marker in _NO_SESSION_MARKERS):
                # This is okay. It just means there's no session yet.
                return []
            raise

        windows = [
            {
                "window_id": str(tab["tab_id"]),
                "window_name": tab["name"],
                "window_active_clients": str(len(tab.get("other_focused_clients", []))),
            }
            for tab in tabs
        ]
        return sorted(windows, key=lambda k: k["window_name"])

    def list_sessions(self) -> List[Dict[str, str]]:
        """Return live sessions sorted by name; EXITED sessions are treated as missing."""
        try:
            cmd = ZellijCmd(self._bin, ["list-sessions", "--no-formatting"])
        except RuntimeError as e:
            if any(marker in str(e) for marker in _NO_SESSION_MARKERS):
                return []
            raise

        sessions = []
        for line in cmd.stdout:
            if not line.strip() or "EXITED" in line:
                continue
            name = line.split()[0]
            # zellij has no session groups, so group mirrors the name. The
            # attached count could come from `-s NAME action list-clients`, but
            # nothing needs it and it costs a call per session; report "0".
            sessions.append(
                {
                    "session_id": name,
                    "session_name": name,
                    "session_attached": "0",
                    "session_group": name,
                }
            )
        return sorted(sessions, key=lambda k: k["session_name"])

    def create_detached_window(self, window_name: str, session_group: str) -> None:
        """Create a new tab in the session without moving any client's focus.

        The tab is created inside _headless_client so that at least one client
        is attached (avoiding zellij-org/zellij#5594). The new tab's viewport is
        then checked: a 0x0 tab means the listed client left before new-tab ran,
        so the tab is closed and recreated once under a forced helper.

        Raises:
            RuntimeError: If the session does not exist, a tab with this
                name already exists (zellij itself allows duplicates; scry
                requires unique names), the helper client cannot attach, or
                the recreated tab still has no size (it is closed first).
        """
        if not self.session_exists(session_group):
            raise RuntimeError(f"Session group {session_group} does not exist")

        if self.window_exists(window_name, session_group):
            raise RuntimeError(f"Window {window_name} already exists in session group {session_group}")

        for force in (False, True):
            with self._headless_client(session_group, force=force):
                tab_id = self._action(session_group, "new-tab", "--no-focus", "--name", window_name).stdout[0].strip()
            if self._tab_has_size(session_group, tab_id):
                return
            # Created with zero clients after all (zellij #5594): it would be
            # discarded on the next attach, so remove it now.
            _LOGGER.warning("zellij created tab %s (%s) with no size; closing it", tab_id, window_name)
            self._action(session_group, "close-tab-by-id", tab_id)
        raise RuntimeError(f"zellij created window {window_name} with no size (zellij-org/zellij#5594)")

    def _tab_has_size(self, session_group: str, tab_id: str) -> bool:
        """Return True if the tab exists and has a nonzero viewport (not a #5594 0x0 tab)."""
        for tab in self._list_tabs(session_group):
            if str(tab["tab_id"]) == tab_id:
                return tab.get("viewport_rows", 0) > 0 and tab.get("viewport_columns", 0) > 0
        return False

    def rename_window(self, window_id: str, new_name: str, session_group: str) -> None:
        """Rename the tab with this tab ID, then verify the rename took effect.

        zellij exits 0 for nonexistent tab IDs, so the tabs are re-listed.

        Raises:
            RuntimeError: If no tab has this ID, or its name is not new_name afterwards.
        """
        self._action(session_group, "rename-tab-by-id", window_id, new_name)
        tabs = {str(tab["tab_id"]): tab["name"] for tab in self._list_tabs(session_group)}
        if window_id not in tabs:
            raise RuntimeError(f"no window with id {window_id} in session group {session_group}")
        if tabs[window_id] != new_name:
            raise RuntimeError(f"rename of window {window_id} to {new_name} did not take effect")

    def attach_window(self, window_id: str, session_group: str) -> None:
        """Attach this terminal to the tab with this tab ID; blocks until detach.

        Only this terminal's client lands on the tab; see _attach.
        """
        self._attach(window_id, session_group)

    def _attach(
        self,
        window_id: str,
        session_group: str,
        stdin_fd: int = 0,
        stdout_fd: int = 1,
        hold_timeout: float = 2.0,
    ) -> None:
        """Attach a new client on a scry-owned pty, direct it to the tab, and proxy to it.

        The terminal's output and input are held (by proxy) until a director
        thread has moved the new client to the tab, hiding the frame of the
        tab it first lands on. Returns when the client exits (the user
        detaches) or stdin reaches EOF, after the director thread finishes.

        Args:
            window_id: The target tab ID (str).
            session_group: The zellij session.
            stdin_fd: Terminal input fd.
            stdout_fd: Terminal output fd.
            hold_timeout: Seconds after which held terminal data is released
                even if direction has not finished.

        Raises:
            RuntimeError: If the tab no longer exists, or the session's
                clients or tabs cannot be listed.
        """
        tab_id = int(window_id)
        # One snapshot: a client seen by either call (including one that
        # attached between them, or one viewing no tab) is pre-existing.
        clients = self._list_clients(session_group)
        tabs = self._list_tabs(session_group)
        if not any(tab["tab_id"] == tab_id for tab in tabs):
            # go-to-tab-by-id silently no-ops on a missing ID.
            raise RuntimeError(f"window {window_id} no longer exists")
        before = _map_client_tabs(tabs)
        known = set(clients) | set(before)
        size = shutil.get_terminal_size()
        client = PtyClient(
            [self._bin, "attach", session_group, "options", "--on-force-close", "detach"],
            size.lines,
            size.columns,
        )
        gate = threading.Event()
        director = threading.Thread(
            target=self._direct_client,
            args=(session_group, tab_id, client, known, before, gate),
            name="zellij-direct-client",
            daemon=True,
        )
        director.start()
        try:
            proxy(client, gate, hold_timeout, stdin_fd, stdout_fd)
        finally:
            # proxy has terminated the client, so the director's next write or
            # liveness check stops it; wait so no action is sent after return.
            director.join()

    def _direct_client(
        self,
        session_group: str,
        tab_id: int,
        client: PtyClient,
        known: Set[str],
        before: Dict[str, int],
        gate: threading.Event,
    ) -> None:
        """Move the newly attached client (and no other) to tab_id; always set gate.

        Waits for the new client to appear, presses the wake keys in it (making
        it the routing target for remote actions), then sends go-to-tab-by-id
        and polls list-tabs for the outcome. If a pre-existing client was
        pulled onto the target tab instead (someone typed in it meanwhile, so
        it was the routing target), a go-to-tab-by-id back to its previous tab
        restores it before retrying. Clients that moved anywhere else did so
        on their user's own initiative and are left alone.
        Gives up after _DIRECT_ATTEMPTS attempts, if the client exits, or on
        any error; it only logs at WARNING or below, because ERROR goes to
        stderr, which is the terminal proxy has in raw mode.

        Args:
            session_group: The zellij session.
            tab_id: The tab to move the new client to.
            client: The new client's PtyClient.
            known: Client IDs attached before the new client.
            before: Each pre-existing client's tab_id before the attach.
            gate: Set on return, releasing proxy's held terminal data.
        """
        try:
            new_id = self._wait_for_new_client(session_group, known, alive=client.is_alive)
            if new_id is None:
                _LOGGER.warning("zellij client did not attach; not directing it to tab %s", tab_id)
                return
            for _ in range(_DIRECT_ATTEMPTS):
                for key in self._wake_keys:
                    client.write(key.encode())
                    time.sleep(_WAKE_KEY_INTERVAL)
                if not client.is_alive():
                    return
                self._action(session_group, "go-to-tab-by-id", str(tab_id))
                if self._confirm_direction(session_group, tab_id, new_id, before):
                    return
            _LOGGER.warning("could not direct zellij client %s to tab %s", new_id, tab_id)
        except OSError as e:
            # The client exited (pty closed) mid-direction.
            _LOGGER.debug("stopped directing zellij client to tab %s: %s", tab_id, e)
        except Exception:  # noqa: BLE001 -- a traceback here would land on the raw-mode terminal
            _LOGGER.warning("failed directing zellij client to tab %s", tab_id, exc_info=True)
        finally:
            gate.set()

    def _confirm_direction(self, session_group: str, tab_id: int, new_id: str, before: Dict[str, int]) -> bool:
        """Poll for the outcome of one go-to-tab-by-id aimed at the new client.

        Returns:
            True once the new client is on tab_id; False if a pre-existing
            client was pulled onto tab_id instead (it has been sent back) or
            the poll timed out.
        """
        deadline = time.monotonic() + _DIRECT_CONFIRM_TIMEOUT
        while True:
            tabs = self._client_tabs(session_group)
            if tabs.get(new_id) == tab_id:
                return True
            for cid, tab in tabs.items():
                if cid != new_id and cid in before and tab == tab_id and before[cid] != tab_id:
                    self._action(session_group, "go-to-tab-by-id", str(before[cid]))
                    return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(_CLIENT_POLL_INTERVAL)

    def ensure_session_group(self, session_group: str) -> None:
        """Create the session, detached, if it does not already exist."""
        if self.session_exists(session_group):
            return
        ZellijCmd(self._bin, ["attach", "--create-background", session_group])
