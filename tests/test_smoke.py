"""Smoke test confirming imports work."""


def test_imports():
    from scry.backends.tmux import TmuxFmtCmd  # noqa: F401
    from scry.scry import format_window_name, validate_window_name  # noqa: F401
