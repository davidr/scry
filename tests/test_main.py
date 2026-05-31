"""Tests for the scry.__main__ entry point."""

import sys
from unittest.mock import patch


def test_importing_main_does_not_launch_loop():
    """Importing the module must not run the interactive loop (latent
    double-invocation: the console-script imports the module AND calls
    run_scry, so an import-time call would run the loop twice)."""
    sys.modules.pop("scry.__main__", None)
    with patch("scry.scry.do_table_loop") as mock_loop:
        import scry.__main__  # noqa: F401

        assert mock_loop.call_count == 0
    sys.modules.pop("scry.__main__", None)


def test_run_scry_invokes_loop_once():
    """run_scry() is the single entry point and runs the loop exactly once."""
    import scry.__main__ as main_mod

    with patch("scry.__main__.do_table_loop") as mock_loop:
        main_mod.run_scry()

        mock_loop.assert_called_once()
