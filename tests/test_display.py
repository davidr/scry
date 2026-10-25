"""Tests for display functions: get_column_width() and format_window_strings()."""

import io
from unittest.mock import patch

from rich.console import Console

from scry.scry import format_window_strings, get_column_width, setup_display

# --- get_column_width() ---


def test_wide_terminal(scry_config):
    """Correct column count and width with large terminal."""
    scry_config["n_cols"] = 4
    scry_config["minnamelen"] = 15
    scry_config["fmt_overhead"] = 3
    with patch("scry.scry.get_terminal_size") as mock_ts:
        mock_ts.return_value.columns = 200
        n_cols, col_width = get_column_width()
    assert n_cols == 4
    assert col_width >= scry_config["fmt_overhead"] + scry_config["minnamelen"] + 3


def test_narrow_terminal(scry_config):
    """Columns reduced when terminal too narrow."""
    scry_config["n_cols"] = 4
    scry_config["minnamelen"] = 15
    scry_config["fmt_overhead"] = 3
    with patch("scry.scry.get_terminal_size") as mock_ts:
        mock_ts.return_value.columns = 50
        n_cols, col_width = get_column_width()
    assert n_cols < 4
    assert col_width >= scry_config["fmt_overhead"] + scry_config["minnamelen"] + 3


# --- format_window_strings() ---


def test_basic_formatting(mock_windows, scry_config, clean_window_history):
    """Output has correct count, each string contains index and name."""
    scry_config["fmt_overhead"] = 3
    result = format_window_strings(40, mock_windows, len(mock_windows))
    assert len(result) == 5
    assert "alpha" in result[0]
    assert "0)" in result[0]


def test_history_highlighting(mock_windows, scry_config, clean_window_history):
    """Most recent -> magenta, 2nd -> green, 3rd -> blue markup."""
    scry_config["fmt_overhead"] = 3
    clean_window_history.extend(["@3", "@2", "@1"])
    result = format_window_strings(40, mock_windows, len(mock_windows))
    # @1 is most recent (index 0 in mock_windows)
    assert "magenta" in result[0]
    # @2 is 2nd most recent (index 1 in mock_windows)
    assert "green" in result[1]
    # @3 is 3rd most recent (index 2 in mock_windows)
    assert "blue" in result[2]


def test_active_client_marker(mock_windows, scry_config, clean_window_history):
    """window_active_clients != '0' produces '#'."""
    scry_config["fmt_overhead"] = 3
    result = format_window_strings(40, mock_windows, len(mock_windows))
    # mock_windows[1] ("beta") has window_active_clients = "1"
    assert "#" in result[1]
    # mock_windows[0] ("alpha") has window_active_clients = "0"
    assert "#" not in result[0]


def test_name_truncation(scry_config, clean_window_history):
    """Long name + small column_width triggers * truncation."""
    scry_config["fmt_overhead"] = 3
    windows = [{"window_id": "@1", "window_name": "very-long-window-name-here", "window_active_clients": "0"}]
    result = format_window_strings(15, windows, 1)
    assert "*" in result[0]


def test_prefix_collapsing(prefix_windows, scry_config, clean_window_history):
    """Consecutive same-prefix windows collapse prefix to spaces."""
    scry_config["fmt_overhead"] = 3
    scry_config["hide_prefixes"] = True
    # With items_per_col = 5 (single column), all are in same column
    result = format_window_strings(40, prefix_windows, 5)
    # First window "proj+01" should show full name
    assert "proj" in result[0]
    # Second window "proj+02" should have prefix collapsed (spaces instead of "proj")
    assert "proj" not in result[1]
    # "02" (the suffix digits) should still be present
    assert "02" in result[1]


def test_no_collapse_at_column_top(prefix_windows, scry_config, clean_window_history):
    """Row 0 never collapses prefix."""
    scry_config["fmt_overhead"] = 3
    scry_config["hide_prefixes"] = True
    # With items_per_col = 2, windows at index 0, 2, 4 are at row 0
    result = format_window_strings(40, prefix_windows, 2)
    # Index 0 is row 0 — always full name
    assert "proj" in result[0]
    # Index 2 is row 0 of column 2 — should also show full name
    assert "proj" in result[2]


def test_no_collapse_when_highlighted(prefix_windows, scry_config, clean_window_history):
    """Highlighted windows don't collapse."""
    scry_config["fmt_overhead"] = 3
    scry_config["hide_prefixes"] = True
    # Make the second prefix window highlighted (most recent in history)
    clean_window_history.append("@11")
    result = format_window_strings(40, prefix_windows, 5)
    # @11 is "proj+02" at index 1 — should NOT collapse because it's highlighted
    assert "proj" in result[1]


def test_no_collapse_when_hide_prefixes_disabled(prefix_windows, scry_config, clean_window_history):
    """With hide_prefixes off, consecutive same-prefix names keep their full prefix."""
    scry_config["fmt_overhead"] = 3
    scry_config["hide_prefixes"] = False
    result = format_window_strings(40, prefix_windows, 5)
    # "proj+02" at index 1 would normally collapse, but the feature is off
    assert "proj" in result[1]


# --- literal rendering ---


def _render(strings):
    """Render markup strings through a real Console and return the plain text."""
    buf = io.StringIO()
    console = Console(file=buf, width=200, color_system=None)
    for s in strings:
        console.print(s, end="")
    return buf.getvalue()


def _named(name, wid="@1"):
    return {"window_id": wid, "window_name": name, "window_active_clients": "0"}


def test_bracket_name_renders_literally(scry_config, clean_window_history):
    """A name like '[/]logs' must not raise MarkupError or be eaten."""
    scry_config["fmt_overhead"] = 3
    out = _render(format_window_strings(40, [_named("[/]logs")], 1))
    assert "[/]logs" in out


def test_markup_like_name_renders_literally(scry_config, clean_window_history):
    """A name like '[bold]x' is shown verbatim."""
    scry_config["fmt_overhead"] = 3
    out = _render(format_window_strings(40, [_named("[bold]x")], 1))
    assert "[bold]x" in out


def test_control_chars_replaced(scry_config, clean_window_history):
    """Newlines and ESC are replaced by '?'."""
    scry_config["fmt_overhead"] = 3
    out = _render(format_window_strings(60, [_named("a\nb\x1b[31mc")], 1))
    assert "a?b?[31mc" in out
    assert "\n" not in out
    assert "\x1b" not in out


def test_unicode_and_leading_dash_names(scry_config, clean_window_history):
    """Ordinary unicode and dash-leading names are unchanged."""
    scry_config["fmt_overhead"] = 3
    out = _render(format_window_strings(40, [_named("-dash", "@1"), _named("日本語", "@2")], 2))
    assert "-dash" in out
    assert "日本語" in out


def test_escaped_name_keeps_column_width(scry_config, clean_window_history):
    """Escape backslashes must not change the padding."""
    scry_config["fmt_overhead"] = 3
    a, b = format_window_strings(30, [_named("[x]", "@1"), _named("abc", "@2")], 2)
    assert len(_render([a])) == len(_render([b]))


def test_error_message_with_brackets_renders():
    """setup_display prints error text literally."""
    buf = io.StringIO()
    console = Console(file=buf, width=100, height=30, color_system=None)
    with patch("scry.scry.sleep"):
        setup_display(console, [], "bad [/] thing")
    assert "Error: bad [/] thing" in buf.getvalue()
