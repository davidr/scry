"""Tests for WINDOW_HISTORY update logic."""

from scry.scry import update_window_history


def test_add_to_empty(clean_window_history):
    """First window appended."""
    update_window_history("@1")
    assert list(clean_window_history) == ["@1"]


def test_append_different(clean_window_history):
    """New window appended to end."""
    update_window_history("@1")
    update_window_history("@2")
    assert list(clean_window_history) == ["@1", "@2"]


def test_no_duplicate_last(clean_window_history):
    """Re-adding last window is a no-op."""
    update_window_history("@1")
    update_window_history("@2")
    update_window_history("@2")
    assert list(clean_window_history) == ["@1", "@2"]


def test_moves_existing_to_end(clean_window_history):
    """Existing window removed from middle, appended to end."""
    update_window_history("@1")
    update_window_history("@2")
    update_window_history("@3")
    update_window_history("@1")
    assert list(clean_window_history) == ["@2", "@3", "@1"]
