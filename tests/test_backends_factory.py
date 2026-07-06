"""Tests for get_backend() and the backend config key."""

import sys
from unittest.mock import patch

import pytest

from scry.backends import get_backend
from scry.backends.tmux import TmuxBackend
from scry.scry import parse_args_and_configure


def test_get_backend_default_is_tmux():
    with patch("scry.backends.tmux.find_bin_in_path", return_value="/usr/bin/tmux"):
        backend = get_backend({})
    assert isinstance(backend, TmuxBackend)


def test_get_backend_tmux_explicit():
    with patch("scry.backends.tmux.find_bin_in_path", return_value="/usr/bin/tmux"):
        backend = get_backend({"backend": "tmux"})
    assert isinstance(backend, TmuxBackend)


def test_get_backend_unknown_raises_valueerror():
    with pytest.raises(ValueError, match="unknown backend"):
        get_backend({"backend": "screen"})


def test_config_backend_defaults_to_tmux():
    with patch.object(sys, "argv", ["scry"]):
        with patch("scry.scry.os.path.exists", return_value=False):
            cfg = parse_args_and_configure()
    assert cfg["backend"] == "tmux"


def test_config_backend_cli_flag():
    with patch.object(sys, "argv", ["scry", "--backend", "zellij"]):
        with patch("scry.scry.os.path.exists", return_value=False):
            cfg = parse_args_and_configure()
    assert cfg["backend"] == "zellij"


def test_config_backend_short_flag():
    with patch.object(sys, "argv", ["scry", "-b", "zellij"]):
        with patch("scry.scry.os.path.exists", return_value=False):
            cfg = parse_args_and_configure()
    assert cfg["backend"] == "zellij"


def test_config_backend_rejects_unknown_cli_value():
    with patch.object(sys, "argv", ["scry", "--backend", "screen"]):
        with patch("scry.scry.os.path.exists", return_value=False):
            with pytest.raises(SystemExit):
                parse_args_and_configure()
