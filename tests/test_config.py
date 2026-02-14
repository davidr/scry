"""Tests for parse_args_and_configure() configuration loading."""

import sys
from unittest.mock import patch

from scry.scry import default_config, parse_args_and_configure


def test_defaults():
    """No args, no config file: returns default_config values."""
    with (
        patch.object(sys, "argv", ["scry"]),
        patch("scry.scry.os.path.exists", return_value=False),
    ):
        cfg = parse_args_and_configure()
    for key, value in default_config.items():
        assert cfg[key] == value


@patch("scry.scry.os.path.exists", return_value=False)
class TestCliArgs:
    """Parametrized: CLI flags override defaults."""

    def test_minnamelen(self, _):
        with patch.object(sys, "argv", ["scry", "--minnamelen", "25"]):
            cfg = parse_args_and_configure()
        assert cfg["minnamelen"] == 25

    def test_columns(self, _):
        with patch.object(sys, "argv", ["scry", "--columns", "6"]):
            cfg = parse_args_and_configure()
        assert cfg["n_cols"] == 6

    def test_session_group(self, _):
        with patch.object(sys, "argv", ["scry", "--session_group", "dev"]):
            cfg = parse_args_and_configure()
        assert cfg["session_group"] == "dev"

    def test_debug(self, _):
        with patch.object(sys, "argv", ["scry", "--debug"]):
            cfg = parse_args_and_configure()
        assert cfg["debug"] is True


def test_config_file_loading(tmp_path):
    """Values from mock ~/.scry.yml applied."""
    config_file = tmp_path / ".scry.yml"
    config_file.write_text("minnamelen: 30\nsession_group: devgroup\n")
    with (
        patch.object(sys, "argv", ["scry"]),
        patch("scry.scry.os.path.join", return_value=str(config_file)),
        patch("scry.scry.os.path.exists", return_value=True),
    ):
        cfg = parse_args_and_configure()
    assert cfg["minnamelen"] == 30
    assert cfg["session_group"] == "devgroup"


def test_cli_overrides_file(tmp_path):
    """CLI args take precedence over file config."""
    config_file = tmp_path / ".scry.yml"
    config_file.write_text("minnamelen: 30\n")
    with (
        patch.object(sys, "argv", ["scry", "--minnamelen", "50"]),
        patch("scry.scry.os.path.join", return_value=str(config_file)),
        patch("scry.scry.os.path.exists", return_value=True),
    ):
        cfg = parse_args_and_configure()
    assert cfg["minnamelen"] == 50
