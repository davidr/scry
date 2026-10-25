"""Tests for the scry.__main__ entry point."""

import subprocess
import sys
from unittest.mock import patch

import pytest


def test_importing_main_does_not_import_scry_scry():
    """Importing the module, and routing a remote subcommand, must not import scry.scry (argv parse + backend build)."""
    code = (
        "import sys\n"
        "sys.argv = ['scry', 'remote-list', '--proto=1']\n"
        "import scry.__main__ as m\n"
        "assert 'scry.scry' not in sys.modules\n"
        "import scry.remote.server as s\n"
        "s.main = lambda argv: 0\n"
        "try:\n"
        "    m.run_scry()\n"
        "except SystemExit as e:\n"
        "    assert e.code == 0\n"
        "assert 'scry.scry' not in sys.modules\n"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_run_scry_invokes_loop_once():
    """run_scry() runs the interactive loop exactly once for a normal invocation."""
    import scry.__main__ as main_mod

    with patch.object(sys, "argv", ["scry"]), patch("scry.scry.do_table_loop") as mock_loop:
        main_mod.run_scry()

        mock_loop.assert_called_once()


def test_run_scry_routes_remote_subcommands():
    """remote-* subcommands go to the headless server and never to the loop."""
    import scry.__main__ as main_mod

    with (
        patch.object(sys, "argv", ["scry", "remote-list", "--proto=1"]),
        patch("scry.remote.server.main", return_value=83) as server_main,
        patch("scry.scry.do_table_loop") as mock_loop,
    ):
        with pytest.raises(SystemExit) as exc:
            main_mod.run_scry()

    assert exc.value.code == 83
    server_main.assert_called_once_with(["remote-list", "--proto=1"])
    mock_loop.assert_not_called()
