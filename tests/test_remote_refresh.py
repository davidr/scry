"""Tests for the parallel remote refresh and exit-code messages."""

import json
import signal
import subprocess
import time
from unittest import mock

import pytest

from scry.remote.cache import read_cache, write_cache
from scry.remote.config import RemoteConfig
from scry.remote.messages import exit_message
from scry.remote.protocol import LIST_BEGIN, LIST_END, frame
from scry.remote.refresh import parse_refresh, refresh_command, refresh_remotes

R = RemoteConfig(key="c", label="Coder", host="coder.build")


def _ok_payload(tabs=None):
    return {
        "proto": 1,
        "status": "ok",
        "session": "main",
        "identity": "1:2",
        "started_at": 1.0,
        "tabs": tabs if tabs is not None else [{"id": 1, "name": "a", "clients": 0}],
    }


class FakeProc:
    """Stand-in for subprocess.Popen: preset output, optional one-shot timeout and delay."""

    next_pid = 1000

    def __init__(self, out=b"", err=b"", returncode=0, timeout=False, delay=0.0):
        FakeProc.next_pid += 1
        self.pid = FakeProc.next_pid
        self._res = (out, err)
        self.returncode = returncode
        self._timeout = timeout
        self._delay = delay
        self.stdout = self.stderr = None
        self.stuck = False
        self.boom = None

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0

    def communicate(self, timeout=None):
        if self.boom:
            raise self.boom
        if self.stuck:
            raise subprocess.TimeoutExpired("ssh", timeout)
        if self._delay:
            time.sleep(self._delay)
        if self._timeout:
            self._timeout = False
            raise subprocess.TimeoutExpired("ssh", timeout)
        return self._res


def _run(remotes, procs, tmp_path, timeout=10.0, guard=True):
    """Run refresh_remotes with Popen returning procs in order; return (message, popen, killpg)."""
    with (
        mock.patch("scry.remote.refresh.subprocess.Popen", side_effect=procs) as popen,
        mock.patch("scry.remote.refresh.os.killpg") as killpg,
    ):
        msg = refresh_remotes(remotes, str(tmp_path), timeout, guard)
    return msg, popen, killpg


def test_refresh_command_argv(tmp_path):
    assert refresh_command(R) == ["ssh", "-T", "-o", "BatchMode=yes", "coder.build", "scry remote-list --proto=1"]
    _, popen, _ = _run([R], [FakeProc(out=frame(_ok_payload()).encode())], tmp_path)
    kwargs = popen.call_args.kwargs
    assert popen.call_args.args[0] == refresh_command(R)
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.PIPE
    assert kwargs["stderr"] == subprocess.PIPE
    assert kwargs["start_new_session"] is True


def test_parse_refresh_ok_no_session_error_proto():
    ok = parse_refresh(R, 0, frame(_ok_payload()).encode(), b"")
    assert ok.status == "ok" and ok.payload["session"] == "main"
    ns = parse_refresh(R, 83, frame({"proto": 1, "status": "no_session"}).encode(), b"")
    assert ns.status == "no_session"
    err = parse_refresh(R, 80, frame({"proto": 1, "status": "error", "error": "boom"}).encode(), b"")
    assert (err.status, err.reason) == ("failed", "boom")
    old = parse_refresh(R, 0, frame({"proto": 2, "status": "ok"}).encode(), b"")
    assert (old.status, old.reason) == ("failed", "scry on host c is incompatible — upgrade it")


def test_parse_refresh_framed_wins_over_exit_code():
    res = parse_refresh(R, 1, frame(_ok_payload()).encode(), b"")
    assert res.status == "ok"


def test_parse_refresh_exit_codes():
    assert parse_refresh(R, 255, b"", b"").reason == "host c unavailable, or the connection was closed"
    assert parse_refresh(R, 127, b"", b"").reason == exit_message(127, "c")
    assert exit_message(127, "c") == "scry not found on host c; set `scry:` in remotes"
    assert parse_refresh(R, 2, b"", b"").reason == "scry on host c is too old for remote mode"
    assert parse_refresh(R, 1, b"", b"").reason == "bad response from host c: no framed response (exit 1)"


def test_parse_refresh_255_auth_hint():
    for text in (b"Permission denied (publickey).", b"Host key verification failed."):
        res = parse_refresh(R, 255, b"", text)
        assert res.reason == "host c unavailable, or the connection was closed; run 'ssh coder.build' by hand once"


def test_exit_message_values():
    assert exit_message(0, "c") == ""
    assert exit_message(81, "c") == "host c's tab list is stale — press z"
    assert exit_message(82, "c") == "that tab is gone on host c — press z"
    assert exit_message(83, "c") == "no zellij session running on host c"
    assert exit_message(84, "c").startswith("scry on host c isn't set up for remote mode")
    assert exit_message(85, "c") == "couldn't identify the zellij session on host c"
    assert exit_message(86, "c") == "scry on host c is incompatible — upgrade it"
    assert exit_message(-signal.SIGINT, "c") == "connection to host c cancelled"
    assert exit_message(99, "c") == "remote command on host c failed (exit 99)"


def test_refresh_writes_each_host(tmp_path):
    b = RemoteConfig(key="b", label="B", host="b.host")
    c = RemoteConfig(key="c", label="C", host="c.host")
    procs = [FakeProc(out=frame(_ok_payload()).encode()), FakeProc(returncode=255)]
    msg, _, _ = _run([b, c], procs, tmp_path)
    assert msg == "c: host c unavailable, or the connection was closed"
    assert read_cache(str(tmp_path), "b.host")["last_attempt"]["status"] == "ok"
    assert read_cache(str(tmp_path), "c.host")["last_attempt"]["status"] == "failed"


def test_refresh_timeout_kills_group(tmp_path):
    proc = FakeProc(timeout=True)
    msg, _, killpg = _run([R], [proc], tmp_path)
    killpg.assert_called_once_with(proc.pid, signal.SIGKILL)
    assert "timed out after 10s" in msg


def test_refresh_timeout_kill_ignores_missing_group(tmp_path):
    proc = FakeProc(timeout=True)
    with (
        mock.patch("scry.remote.refresh.subprocess.Popen", return_value=proc),
        mock.patch("scry.remote.refresh.os.killpg", side_effect=ProcessLookupError),
    ):
        msg = refresh_remotes([R], str(tmp_path), 10, True)
    assert "timed out" in msg


def test_refresh_timeout_mid_output_keeps_tabs(tmp_path):
    old = {"host": "coder.build", "identity": "1:2", "tabs": [{"id": 1, "name": "a", "clients": 0}]}
    write_cache(str(tmp_path), "coder.build", old)
    proc = FakeProc(out=(LIST_BEGIN + '\n{"proto": 1').encode(), timeout=True)
    _run([R], [proc], tmp_path)
    cache = read_cache(str(tmp_path), "coder.build")
    assert cache["tabs"] == old["tabs"]
    assert cache["last_attempt"]["status"] == "failed"
    assert LIST_END not in json.dumps(cache)


def test_refresh_runs_hosts_in_parallel(tmp_path):
    hosts = [RemoteConfig(key=k, label=k, host=f"{k}.h") for k in "ab"]
    procs = [FakeProc(out=frame(_ok_payload()).encode(), delay=0.3) for _ in hosts]
    start = time.monotonic()
    _run(hosts, procs, tmp_path)
    assert time.monotonic() - start < 0.5


def test_refresh_ctrl_c_kills_workers_keeps_cache(tmp_path):
    hosts = [RemoteConfig(key=k, label=k, host=f"{k}.h") for k in "ab"]
    for h in hosts:
        write_cache(str(tmp_path), h.host, {"host": h.host, "tabs": []})
    before = {h.host: (tmp_path / f"{h.host}.json").read_bytes() for h in hosts}
    procs = [FakeProc(out=frame(_ok_payload()).encode(), delay=0.2) for _ in hosts]
    with mock.patch("scry.remote.refresh._wait_workers", side_effect=KeyboardInterrupt):
        msg, _, killpg = _run(hosts, procs, tmp_path)
    assert msg == "refresh cancelled"
    assert {c.args[0] for c in killpg.call_args_list} == {p.pid for p in procs}
    assert {h.host: (tmp_path / f"{h.host}.json").read_bytes() for h in hosts} == before


def test_refresh_ctrl_c_unguarded_propagates(tmp_path):
    with mock.patch("scry.remote.refresh._wait_workers", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            _run([R], [FakeProc(delay=0.1)], tmp_path, guard=False)


def test_refresh_unwritable_cache_reports_message(tmp_path):
    with mock.patch("scry.remote.refresh.write_cache", side_effect=PermissionError(13, "Permission denied")):
        msg, _, _ = _run([R], [FakeProc(out=frame(_ok_payload()).encode())], tmp_path)
    assert "c: cannot write cache (Permission denied)" in msg


def test_refresh_no_remotes(tmp_path):
    assert refresh_remotes([], str(tmp_path), 10, True) == "no remotes configured"


def test_refresh_ssh_missing_reports_failure(tmp_path):
    with mock.patch("scry.remote.refresh.subprocess.Popen", side_effect=FileNotFoundError(2, "No such file")):
        msg = refresh_remotes([R], str(tmp_path), 10, True)
    assert msg == "c: cannot run ssh (No such file)"


def test_refresh_post_kill_reap_is_bounded(tmp_path):
    proc = FakeProc()
    proc.stuck = True
    start = time.monotonic()
    with mock.patch("scry.remote.refresh.REAP_TIMEOUT", 0.05):
        msg, _, killpg = _run([R], [proc], tmp_path)
    assert time.monotonic() - start < 1
    killpg.assert_called_once()
    assert "timed out after 10s" in msg


def test_refresh_worker_exception_becomes_failure(tmp_path):
    proc = FakeProc()
    proc.boom = OSError(5, "I/O error")
    msg, _, _ = _run([R], [proc], tmp_path)
    assert msg.startswith("c: refresh error on host c")
    assert read_cache(str(tmp_path), "coder.build")["last_attempt"]["status"] == "failed"


def test_refresh_oserror_without_strerror(tmp_path):
    with mock.patch("scry.remote.refresh.subprocess.Popen", side_effect=OSError("odd")):
        msg = refresh_remotes([R], str(tmp_path), 10, True)
    assert msg == "c: cannot run ssh (odd)"
