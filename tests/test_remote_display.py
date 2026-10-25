"""Tests for remote sections in the index display."""

import io
from unittest.mock import patch

from rich.console import Console

from scry.remote.cache import RemoteSection, RemoteTabRef
from scry.remote.config import RemoteConfig
from scry.scry import (
    draw_table_windows,
    format_age,
    format_window_strings,
    prompt_hint,
    section_header,
    setup_display,
    show_help,
)

NOW = 1_000_000.0


def make_console(height=40):
    return Console(file=io.StringIO(), width=120, height=height, color_system=None, force_terminal=False)


def make_section(key="b", state="ok", names=("alpha", "beta"), last_success=NOW - 300, clients="0", label=None):
    remote = RemoteConfig(key=key, label=label or f"host-{key}", host=f"{key}.example")
    rows = [
        {"window_id": RemoteTabRef(remote.host, "1:2", str(i + 5)), "window_name": n, "window_active_clients": clients}
        for i, n in enumerate(names)
    ]
    return RemoteSection(remote=remote, state=state, last_success=last_success, reason="", rows=rows)


def draw(sections, windows=(), height=40):
    console = make_console(height)
    with patch("scry.scry.get_terminal_size") as ts:
        ts.return_value.columns = 120
        draw_table_windows(console, list(windows), sections)
    return console.file.getvalue()


def test_format_age_buckets():
    assert format_age(0) == "<1m ago"
    assert format_age(59) == "<1m ago"
    assert format_age(60) == "1m ago"
    assert format_age(3599) == "59m ago"
    assert format_age(3600) == "1h ago"
    assert format_age(86399) == "23h ago"
    assert format_age(86400) == "1d ago"
    assert format_age(3 * 86400 + 5) == "3d ago"


def test_header_ok():
    h = section_header(make_section(label="build"), 60, NOW)
    assert h.plain.startswith("── b · build ")
    assert h.plain.endswith(" 5m ago")
    assert len(h.plain) == 60
    assert "─" in h.plain[12:-8]


def test_header_failed_dim():
    h = section_header(make_section(state="failed"), 60, NOW)
    assert h.plain.endswith("unavailable · 5m ago")
    assert h.style == "dim"
    never = section_header(make_section(state="failed", last_success=None), 60, NOW)
    assert never.plain.endswith(" unavailable")


def test_header_no_session():
    h = section_header(make_section(state="no_session"), 100, NOW)
    assert h.plain.endswith("no session running — start it, then press z")


def test_header_never():
    h = section_header(make_section(state="never", last_success=None), 100, NOW)
    assert h.plain.endswith("never refreshed — press z")


def test_header_label_with_markup_literal():
    h = section_header(make_section(label="[red]x"), 60, NOW)
    assert "[red]x" in h.plain
    out = draw([make_section(label="[red]x")])
    assert "[red]x" in out


def test_header_tolerates_odd_timestamps():
    future = section_header(make_section(last_success=NOW + 100), 60, NOW)
    assert future.plain.endswith("<1m ago")
    for bad in (float("inf"), float("nan")):
        h = section_header(make_section(state="failed", last_success=bad), 60, NOW)
        assert h.plain.endswith(" unavailable")
        section_header(make_section(last_success=bad), 60, NOW)


def test_header_narrow_width_does_not_raise():
    h = section_header(make_section(state="no_session"), 20, NOW)
    assert h.plain.startswith("── b")


def test_remote_rows_labelled_b0_b1_in_name_order():
    out = draw([make_section()])
    assert any(line.startswith("── b · host-b") for line in out.splitlines())
    assert "b0) alpha" in out
    assert "b1) beta" in out
    assert out.index("b0)") < out.index("b1)")


def test_remote_labels_right_justified():
    out = draw([make_section(names=tuple(f"t{i}" for i in range(11)))])
    assert " b0)" in out
    assert "b10)" in out


def test_remote_row_hash_from_cached_clients():
    assert "b0)#alpha" in draw([make_section(clients="2")])
    assert "b0) alpha" in draw([make_section(clients="0")])


def test_remote_history_highlight(clean_window_history, scry_config):
    clean_window_history.append(RemoteTabRef("h", "1:2", "5"))
    rows = [{"window_id": RemoteTabRef("h", "1:2", "5"), "window_name": "x", "window_active_clients": "0"}]
    result = format_window_strings(40, rows, 1, labels=["b0"])
    assert result[0].startswith("[bold reverse magenta]")


def test_remote_name_with_newline_and_brackets_renders():
    out = draw([make_section(names=("a\nb[red]c", "[/]"))])
    assert "a?b[red]c" in out
    assert "[/]" in out


def test_unavailable_rows_dim(scry_config):
    section = make_section(state="failed")
    rows = format_window_strings(40, section.rows, 2, labels=["b0", "b1"], dim=True)
    assert all(r.startswith("[dim]") for r in rows)
    plain = format_window_strings(40, section.rows, 2, labels=["b0", "b1"])
    assert not any(r.startswith("[dim]") for r in plain)


def test_local_and_remote_both_drawn(mock_windows):
    out = draw([make_section()], windows=mock_windows)
    assert "0) alpha" in out
    assert "b0) alpha" in out


def test_prompt_hint_examples():
    with_rows = make_section(names=("a", "b", "c", "d"))
    empty = make_section(key="c", names=())
    assert prompt_hint(8, [with_rows, empty]) == "0-7/b0-b3/n/r/s/u/d/l/z/q/?"
    assert prompt_hint(8, []) == "0-7/n/r/s/u/d/l/q/?"
    assert prompt_hint(0, []) == "##/n/r/s/u/d/l/q/?"


def test_setup_display_overflowing_height_does_not_raise():
    sections = [make_section(key=k, names=tuple(f"t{i}" for i in range(20))) for k in "bcd"]
    console = make_console(height=10)
    with patch("scry.scry.get_terminal_size") as ts, patch("scry.scry.sleep"):
        ts.return_value.columns = 120
        setup_display(console, [], "", sections)


def test_help_mentions_remote_escapes_only_when_configured():
    def helptext(*args):
        console = make_console()
        with patch.object(Console, "input", return_value=""):
            show_help(console, *args)
        return console.file.getvalue()

    assert "~." not in helptext()
    assert "~." not in helptext(False)
    on = helptext(True)
    assert "Attach remote tab" in on
    assert "Refresh remote tabs" in on
    assert "Ctrl-C while connecting" in on and "~." in on


def test_highlighted_row_in_failed_section_is_dim_and_coloured(clean_window_history, scry_config):
    section = make_section(state="failed")
    clean_window_history.append(section.rows[0]["window_id"])
    rows = format_window_strings(40, section.rows, 2, labels=["b0", "b1"], dim=True)
    assert rows[0].startswith("[dim][bold reverse magenta]")
    console = Console(file=io.StringIO(), width=120, force_terminal=True, color_system="standard")
    console.print(rows[0], end="")
    out = console.file.getvalue()
    # SGR 2 (dim) and 7 (reverse) both present in the same styled run
    assert "\x1b[1;2;" in out or "\x1b[2;" in out or ";2;" in out or "\x1b[1;2m" in out
    assert "7" in out.split("m", 1)[0]


def test_long_header_stays_one_line():
    console = Console(file=io.StringIO(), width=30, color_system=None, force_terminal=False)
    console.print(section_header(make_section(label="x" * 80), 30, NOW))
    assert len(console.file.getvalue().splitlines()) == 1
