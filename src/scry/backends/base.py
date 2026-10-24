# -*- coding: utf-8 -*-
"""Abstract interface that every multiplexer backend implements."""

import contextlib
from abc import ABC, abstractmethod
from typing import ContextManager, Dict, List


class MuxBackend(ABC):
    """The operations scry needs from a terminal multiplexer.

    Listing methods return dicts with normalized keys (window_id, window_name,
    window_active_clients / session_id, session_name, session_attached,
    session_group) and return [] when the server or session does not exist
    yet. All other failures are raised as RuntimeError carrying the
    underlying CLI's stderr.
    """

    @abstractmethod
    def list_windows(self, session_group: str) -> List[Dict[str, str]]:
        """Return the group's windows, sorted by window_name."""

    @abstractmethod
    def list_sessions(self) -> List[Dict[str, str]]:
        """Return all sessions, sorted by session_name."""

    @abstractmethod
    def create_detached_window(self, window_name: str, session_group: str) -> None:
        """Create a window without attaching to it.

        Raises:
            RuntimeError: if the session group does not exist or a window
                with this name already exists.
        """

    @abstractmethod
    def rename_window(self, window_id: str, new_name: str, session_group: str) -> None:
        """Rename a window."""

    @abstractmethod
    def attach_window(self, window_id: str, session_group: str) -> None:
        """Attach this client to a window; blocks until the client detaches."""

    @abstractmethod
    def ensure_session_group(self, session_group: str) -> None:
        """Create the session group, detached, if it does not already exist."""

    def creating_windows(self, session_group: str) -> ContextManager[None]:
        """Return a context manager to wrap a batch of create_detached_window calls.

        Backends that need per-batch setup (e.g. zellij's helper client) override
        this; the default does nothing.
        """
        return contextlib.nullcontext()

    def window_exists(self, window_name: str, session_group: str) -> bool:
        """Check whether a window with this name exists in the group."""
        return window_name in [w["window_name"] for w in self.list_windows(session_group)]

    def session_exists(self, session_name: str) -> bool:
        """Check whether a session with this name exists."""
        return session_name in [s["session_name"] for s in self.list_sessions()]
