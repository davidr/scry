"""Tests for scry.bin_utils.find_bin_in_path."""

import os
from unittest.mock import patch


def test_find_bin_in_path_found(real_find_bin_in_path):
    """Returns full path when binary exists and is executable."""
    with (
        patch.dict(os.environ, {"PATH": "/usr/bin:/usr/local/bin"}),
        patch("scry.bin_utils.os.access", return_value=True),
    ):
        result = real_find_bin_in_path("tmux")
        assert result == "/usr/bin/tmux"


def test_find_bin_in_path_not_found(real_find_bin_in_path):
    """Raises ValueError when binary not in PATH."""
    with (
        patch.dict(os.environ, {"PATH": "/usr/bin:/usr/local/bin"}),
        patch("scry.bin_utils.os.access", return_value=False),
    ):
        try:
            real_find_bin_in_path("nonexistent")
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "nonexistent" in str(e)
