"""Tests for pure functions in scry.scry and scry.tmuxcmd."""

import pytest

from scry.scry import format_window_name, validate_window_name
from scry.tmuxcmd import TmuxFmtCmd

# --- format_window_name ---


def test_format_window_name_short_unchanged():
    """Names <= maxlen returned as-is."""
    assert format_window_name("short", 10) == "short"


def test_format_window_name_exact_length():
    """Name == maxlen returned as-is."""
    assert format_window_name("exact", 5) == "exact"


def test_format_window_name_truncates():
    """Long name truncated with * in middle, correct length."""
    result = format_window_name("abcdefghij", 7)
    assert len(result) == 7
    assert "*" in result


def test_format_window_name_preserves_ends():
    """First half from start, second half from end."""
    result = format_window_name("abcdefghij", 8)
    assert result.startswith("abcd")
    assert result.endswith("hij")
    assert "*" in result


def test_format_window_name_odd_maxlen():
    """Correct splitting with odd maxlen."""
    result = format_window_name("abcdefghijklmno", 9)
    assert len(result) == 9
    assert "*" in result
    # startchars = 9 // 2 = 4, so first 4 chars preserved
    assert result[:4] == "abcd"


# --- validate_window_name ---


@pytest.mark.parametrize(
    "name,expected",
    [
        ("foo", True),
        ("a-b", True),
        ("a.b", True),
        ("a+b", True),
        ("a_b", True),
        ("a b", False),
        ("a@b", False),
        ("", False),
        ("a:b", False),
    ],
)
def test_validate_window_name(name, expected):
    """Parametrized: valid and invalid window names."""
    assert validate_window_name(name) is expected


# --- _format_tmux_keys (static method) ---


def test_format_tmux_keys_single():
    """Single key formatted correctly."""
    assert TmuxFmtCmd._format_tmux_keys(["key"]) == "#{key}"


def test_format_tmux_keys_multiple():
    """Multiple keys joined with __X__ separator."""
    assert TmuxFmtCmd._format_tmux_keys(["a", "b"]) == "#{a}__X__#{b}"
