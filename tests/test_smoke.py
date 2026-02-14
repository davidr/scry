"""Smoke test confirming imports work."""


def test_imports():
    from scry.scry import format_window_name, validate_window_name  # noqa: F401
    from scry.tmuxcmd import TmuxFmtCmd  # noqa: F401
