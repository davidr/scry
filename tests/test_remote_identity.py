"""Tests for session identity discovery from a fake /proc tree."""

import os

import pytest

from scry.remote.identity import IdentityError, find_session_identity


def _proc(root, pid, argv, comm="zellij", starttime=12345):
    d = root / str(pid)
    d.mkdir()
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    (d / "stat").write_text(f"{pid} ({comm}) S " + " ".join(["0"] * 18) + f" {starttime} 0 0\n")


@pytest.fixture
def proc(tmp_path):
    (tmp_path / "stat").write_text("cpu 1 2 3\nbtime 1700000000\n")
    return tmp_path


SERVER = ["/opt/zellij", "--server", "/run/user/1000/zellij/0.45.1/main"]


def test_finds_server_by_exact_argv(proc):
    _proc(proc, 101, SERVER)
    ident = find_session_identity("main", str(proc))
    assert ident.pid == 101
    assert ident.token == "101:12345"


def test_ignores_clients_and_shells(proc):
    _proc(proc, 101, SERVER)
    _proc(proc, 102, ["zellij", "attach", "main"])
    _proc(proc, 103, ["bash", "-c", "zellij --server main"])
    _proc(proc, 104, ["zellij", "--server", "/x/main-old"])
    assert find_session_identity("main", str(proc)).token == "101:12345"


def test_missing_server_raises(proc):
    _proc(proc, 102, ["zellij", "attach", "main"])
    with pytest.raises(IdentityError, match="no zellij server for session main"):
        find_session_identity("main", str(proc))


def test_ambiguous_raises(proc):
    _proc(proc, 101, SERVER)
    _proc(proc, 102, ["zellij", "--server", "/other/main"])
    with pytest.raises(IdentityError, match="ambiguous"):
        find_session_identity("main", str(proc))


def test_comm_with_spaces_and_parens(proc):
    _proc(proc, 101, SERVER, comm="zel (lij) x", starttime=777)
    assert find_session_identity("main", str(proc)).starttime == 777


def test_unreadable_stat_raises(proc):
    _proc(proc, 101, SERVER)
    os.remove(proc / "101" / "stat")
    with pytest.raises(IdentityError):
        find_session_identity("main", str(proc))


def test_vanished_process_skipped(proc):
    _proc(proc, 101, SERVER)
    (proc / "999").mkdir()
    assert find_session_identity("main", str(proc)).pid == 101


def test_started_at_formatting(proc):
    _proc(proc, 101, SERVER)
    started = find_session_identity("main", str(proc)).started_at
    assert started and started != "unknown"
