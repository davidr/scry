"""Tests for the MuxBackend abstract base class."""

from typing import Dict, List

import pytest

from scry.backends.base import MuxBackend


class FakeBackend(MuxBackend):
    """Minimal concrete backend for exercising the ABC's shared helpers."""

    def __init__(self, windows, sessions):
        self._windows = windows
        self._sessions = sessions

    def list_windows(self, session_group: str) -> List[Dict[str, str]]:
        return self._windows

    def list_sessions(self) -> List[Dict[str, str]]:
        return self._sessions

    def create_detached_window(self, window_name: str, session_group: str) -> None:
        pass

    def rename_window(self, window_id: str, new_name: str, session_group: str) -> None:
        pass

    def attach_window(self, window_id: str, session_group: str) -> None:
        pass

    def ensure_session_group(self, session_group: str) -> None:
        pass


def test_muxbackend_cannot_be_instantiated():
    with pytest.raises(TypeError):
        MuxBackend()


def test_window_exists_uses_list_windows():
    backend = FakeBackend(
        windows=[{"window_id": "@1", "window_name": "alpha", "window_active_clients": "0"}],
        sessions=[],
    )
    assert backend.window_exists("alpha", "main") is True
    assert backend.window_exists("missing", "main") is False


def test_session_exists_uses_list_sessions():
    backend = FakeBackend(
        windows=[],
        sessions=[{"session_id": "$0", "session_name": "main", "session_attached": "1", "session_group": "main"}],
    )
    assert backend.session_exists("main") is True
    assert backend.session_exists("missing") is False
