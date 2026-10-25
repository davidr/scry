"""Tests for the per-host remote cache files and section model."""

import os
import threading

import pytest

from scry.remote.cache import (
    RefreshResult,
    RemoteTabRef,
    apply_result,
    cache_path,
    load_sections,
    read_cache,
    write_cache,
)
from scry.remote.config import RemoteConfig


def _payload(tabs, identity="10:20"):
    return {"proto": 1, "status": "ok", "session": "main", "identity": identity, "started_at": 1.0, "tabs": tabs}


def _tab(i, name, clients=0):
    return {"id": i, "name": name, "clients": clients}


def _cfg(key, host):
    return RemoteConfig(key=key, label=host, host=host)


def test_cache_path_sanitized(tmp_path):
    assert cache_path(str(tmp_path), "coder.build") == os.path.join(str(tmp_path), "coder.build.json")
    assert cache_path(str(tmp_path), "user@h:22") == os.path.join(str(tmp_path), "user_h_22.json")


def test_read_missing_empty_corrupt_returns_none(tmp_path):
    d = str(tmp_path)
    assert read_cache(d, "missing") is None
    (tmp_path / "empty.json").write_text("")
    (tmp_path / "bad.json").write_text("{not json")
    (tmp_path / "list.json").write_text("[1]")
    assert read_cache(d, "empty") is None
    assert read_cache(d, "bad") is None
    assert read_cache(d, "list") is None


def test_write_cache_creates_dir(tmp_path):
    d = str(tmp_path / "a" / "b")
    write_cache(d, "h", {"x": 1})
    assert read_cache(d, "h") == {"x": 1}


def test_write_cache_unwritable_raises_oserror(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    d = tmp_path / "ro"
    d.mkdir()
    d.chmod(0o500)
    try:
        with pytest.raises(OSError):
            write_cache(str(d), "h", {"x": 1})
    finally:
        d.chmod(0o700)


def test_write_cache_is_atomic_and_leaves_no_temp_files(tmp_path):
    d = str(tmp_path)
    write_cache(d, "h", {"n": 0, "pad": "x" * 10000})
    reads = []

    def writer(n):
        for i in range(50):
            write_cache(d, "h", {"n": n * 100 + i, "pad": "x" * 10000})

    def reader():
        for _ in range(100):
            reads.append(read_cache(d, "h"))

    threads = [threading.Thread(target=writer, args=(1,)), threading.Thread(target=writer, args=(2,))]
    threads.append(threading.Thread(target=reader))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(reads) == 100
    assert all(isinstance(r, dict) for r in reads)
    assert os.listdir(d) == ["h.json"]


def test_apply_ok_replaces():
    old = apply_result(None, "h", RefreshResult("ok", payload=_payload([_tab(1, "old")])), 100.0)
    new = apply_result(old, "h", RefreshResult("ok", payload=_payload([_tab(2, "new")], "11:21")), 200.0)
    assert new["host"] == "h"
    assert new["identity"] == "11:21"
    assert new["tabs"] == [_tab(2, "new")]
    assert new["last_success"] == 200.0
    assert new["last_attempt"] == {"status": "ok", "reason": "", "at": 200.0}


def test_apply_no_session_clears_tabs_keeps_last_success():
    old = apply_result(None, "h", RefreshResult("ok", payload=_payload([_tab(1, "a")])), 100.0)
    new = apply_result(old, "h", RefreshResult("no_session", "no session"), 200.0)
    assert new["tabs"] == []
    assert new["identity"] is None
    assert new["last_success"] == 100.0
    assert new["last_attempt"] == {"status": "no_session", "reason": "no session", "at": 200.0}


def test_apply_failed_keeps_tabs():
    old = apply_result(None, "h", RefreshResult("ok", payload=_payload([_tab(1, "a")])), 100.0)
    new = apply_result(old, "h", RefreshResult("failed", "timeout"), 200.0)
    assert new["tabs"] == [_tab(1, "a")]
    assert new["identity"] == "10:20"
    assert new["last_success"] == 100.0
    assert new["last_attempt"] == {"status": "failed", "reason": "timeout", "at": 200.0}


def test_apply_failed_with_no_old_cache():
    new = apply_result(None, "h", RefreshResult("failed", "boom"), 5.0)
    assert new["tabs"] == []
    assert new["last_success"] is None


def test_load_sections_sorted_by_name_then_id(tmp_path):
    d = str(tmp_path)
    tabs = [_tab(3, "b", 1), _tab(9, "a"), _tab(2, "a")]
    write_cache(d, "h", apply_result(None, "h", RefreshResult("ok", payload=_payload(tabs)), 1.0))
    (sec,) = load_sections([_cfg("a", "h")], d)
    assert [r["window_id"] for r in sec.rows] == [
        RemoteTabRef("h", "10:20", "2"),
        RemoteTabRef("h", "10:20", "9"),
        RemoteTabRef("h", "10:20", "3"),
    ]
    assert [r["window_name"] for r in sec.rows] == ["a", "a", "b"]
    assert sec.rows[2]["window_active_clients"] == "1"


def test_load_sections_states(tmp_path):
    d = str(tmp_path)
    ok = apply_result(None, "ok", RefreshResult("ok", payload=_payload([_tab(1, "a")])), 1.0)
    write_cache(d, "ok", ok)
    write_cache(d, "failed", apply_result(ok, "failed", RefreshResult("failed", "timeout"), 2.0))
    write_cache(d, "ns", apply_result(ok, "ns", RefreshResult("no_session", "gone"), 2.0))
    remotes = [_cfg("a", "never"), _cfg("b", "ok"), _cfg("c", "failed"), _cfg("d", "ns")]
    secs = load_sections(remotes, d)
    assert [s.remote.host for s in secs] == ["never", "ok", "failed", "ns"]
    assert [s.state for s in secs] == ["never", "ok", "failed", "no_session"]
    assert secs[0].rows == [] and secs[0].last_success is None
    assert len(secs[1].rows) == 1
    assert len(secs[2].rows) == 1 and secs[2].reason == "timeout" and secs[2].last_success == 1.0
    assert secs[3].rows == [] and secs[3].reason == "gone"


def test_load_sections_tolerates_malformed_cache(tmp_path):
    (tmp_path / "h.json").write_text('{"identity": "1:2", "tabs": [{"id": "x"}, 5], "last_attempt": 3}')
    (sec,) = load_sections([_cfg("a", "h")], str(tmp_path))
    assert sec.rows == []


def test_load_sections_tolerates_infinite_numbers(tmp_path):
    (tmp_path / "h.json").write_text(
        '{"identity": "1:2", "last_success": Infinity, "tabs": ['
        '{"id": Infinity, "name": "a"}, {"id": 1, "name": "b", "clients": 1e999}, '
        '{"id": 2, "name": "ok", "clients": 1}]}'
    )
    (sec,) = load_sections([_cfg("a", "h")], str(tmp_path))
    assert [r["window_name"] for r in sec.rows] == ["ok"]


def test_letter_reassignment_reads_other_file(tmp_path):
    d = str(tmp_path)
    old = apply_result(None, "coder.old", RefreshResult("ok", payload=_payload([_tab(1, "a")])), 1.0)
    write_cache(d, "coder.old", old)
    (sec,) = load_sections([_cfg("b", "coder.new")], d)
    assert sec.state == "never"
    assert sec.rows == []
