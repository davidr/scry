"""Shared fixtures and import-time patching for scry tests."""

import sys
from unittest.mock import create_autospec, patch

# Neutralize import-time side effects BEFORE any scry module is imported:
# 1. sys.argv — prevents argparse from reading test runner args
# 2. find_bin_in_path — prevents tmux binary lookup at import time

_argv_patch = patch.object(sys, "argv", ["scry"])
_bin_patch = patch("scry.bin_utils.find_bin_in_path", return_value="/usr/bin/tmux")
_argv_patch.start()
_bin_patch.start()

_real_find_bin_in_path = _bin_patch.temp_original

# Now it's safe to import scry modules
import pytest  # noqa: E402
from rich.console import Console  # noqa: E402

from scry.scry import WINDOW_HISTORY, config  # noqa: E402


@pytest.fixture
def mock_windows():
    """List of 5 window dicts with varied names, one with active clients."""
    return [
        {"window_id": "@1", "window_name": "alpha", "window_active_clients": "0"},
        {"window_id": "@2", "window_name": "beta", "window_active_clients": "1"},
        {"window_id": "@3", "window_name": "gamma", "window_active_clients": "0"},
        {"window_id": "@4", "window_name": "delta", "window_active_clients": "0"},
        {"window_id": "@5", "window_name": "epsilon", "window_active_clients": "0"},
    ]


@pytest.fixture
def mock_sessions():
    """List of 3 session dicts: one attached, one unattached 8-digit, one non-numeric."""
    return [
        {
            "session_id": "$0",
            "session_name": "main",
            "session_attached": "1",
            "session_group": "main",
        },
        {
            "session_id": "$1",
            "session_name": "12345678",
            "session_attached": "0",
            "session_group": "main",
        },
        {
            "session_id": "$2",
            "session_name": "devbox",
            "session_attached": "0",
            "session_group": "other",
        },
    ]


@pytest.fixture
def prefix_windows():
    """Windows with shared prefixes for collapse testing."""
    return [
        {"window_id": "@10", "window_name": "proj+01", "window_active_clients": "0"},
        {"window_id": "@11", "window_name": "proj+02", "window_active_clients": "0"},
        {"window_id": "@12", "window_name": "proj+03", "window_active_clients": "0"},
        {"window_id": "@13", "window_name": "other+01", "window_active_clients": "0"},
        {"window_id": "@14", "window_name": "other+02", "window_active_clients": "0"},
    ]


@pytest.fixture
def clean_window_history():
    """Clear WINDOW_HISTORY before test, restore after."""
    saved = list(WINDOW_HISTORY)
    WINDOW_HISTORY.clear()
    yield WINDOW_HISTORY
    WINDOW_HISTORY.clear()
    WINDOW_HISTORY.extend(saved)


@pytest.fixture
def scry_config():
    """Yield config dict, restore original values after test."""
    saved = config.copy()
    yield config
    config.clear()
    config.update(saved)


@pytest.fixture
def real_find_bin_in_path():
    """Provide the real find_bin_in_path function (not the import-time mock)."""
    return _real_find_bin_in_path


@pytest.fixture
def mock_console():
    """create_autospec(Console) with mock size attribute."""
    console = create_autospec(Console, instance=True)
    console.size = type("Size", (), {"width": 120, "height": 40})()
    return console
