"""Tests for the pseudo-terminal client and gated terminal proxy.

These run plain commands (sh, cat, sleep, stty) on real ptys; no multiplexer is used.
"""

import errno
import fcntl
import os
import select
import signal
import struct
import termios
import threading
import time
from unittest.mock import patch

import pytest

from scry.backends.pty_client import PtyClient, proxy

_JOIN_TIMEOUT = 10.0
""" float: upper bound for any proxy thread to finish before a test fails
"""


@pytest.fixture
def spawn():
    """Spawn PtyClients that are always terminated at teardown."""
    clients = []

    def _spawn(argv, rows=24, cols=80):
        client = PtyClient(argv, rows, cols)
        clients.append(client)
        return client

    yield _spawn
    for client in clients:
        client.terminate()


@pytest.fixture
def fds():
    """Track raw fds (pipes, ptys) and close any still open at teardown."""
    opened = []

    def _track(*new_fds):
        opened.extend(new_fds)
        return new_fds

    yield _track
    for fd in opened:
        try:
            os.close(fd)
        except OSError:
            pass


@pytest.fixture
def pipes(fds):
    """stdin and stdout pipe pairs for proxy: (stdin_r, stdin_w, stdout_r, stdout_w)."""
    stdin_r, stdin_w = fds(*os.pipe())
    stdout_r, stdout_w = fds(*os.pipe())
    os.set_blocking(stdout_r, False)
    return stdin_r, stdin_w, stdout_r, stdout_w


def _read_master_until_eof(fd: int, timeout: float = 5.0) -> bytes:
    """Read a pty master until the child side closes (EIO/EOF) or timeout."""
    out = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.05)
        if not ready:
            continue
        try:
            data = os.read(fd, 4096)
        except OSError as e:
            if e.errno == errno.EIO:
                break
            raise
        if not data:
            break
        out += data
    return bytes(out)


def _read_pipe_until(fd: int, needle: bytes, timeout: float) -> bytes:
    """Read a non-blocking pipe until needle appears, EOF, or timeout."""
    out = bytearray()
    deadline = time.monotonic() + timeout
    while needle not in out and time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.02)
        if not ready:
            continue
        data = os.read(fd, 4096)
        if not data:
            break
        out += data
    return bytes(out)


def _pipe_is_empty(fd: int) -> bool:
    """Return True if a non-blocking pipe has no data to read right now."""
    try:
        data = os.read(fd, 4096)
    except BlockingIOError:
        return True
    return not data


class _ProxyThread(threading.Thread):
    """Run proxy() in a background thread, capturing any exception."""

    def __init__(self, *args, **kwargs):
        super().__init__(daemon=True)
        self._args = args
        self._kwargs = kwargs
        self.error = None

    def run(self):
        try:
            proxy(*self._args, **self._kwargs)
        except BaseException as e:  # noqa: BLE001 — surfaced to the test
            self.error = e

    def finish(self, timeout: float = _JOIN_TIMEOUT):
        self.join(timeout)
        assert not self.is_alive(), "proxy did not return"
        if self.error is not None:
            raise self.error


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


# --- PtyClient ---


def test_ptyclient_reports_size(spawn):
    """The child sees the pty size given at construction."""
    client = spawn(["sh", "-c", "stty size"], 30, 100)
    assert b"30 100" in _read_master_until_eof(client.master_fd)


def test_ptyclient_resize(spawn):
    """resize() changes the size the child sees."""
    client = spawn(["sh", "-c", "sleep 0.3; stty size"])
    client.resize(40, 120)
    assert b"40 120" in _read_master_until_eof(client.master_fd)


def test_ptyclient_resize_signals_child(spawn):
    """The pty is the child's controlling terminal, so resize() delivers SIGWINCH."""
    client = spawn(["sh", "-c", "trap 'stty size; kill $!; exit' WINCH; sleep 5 & wait"])
    time.sleep(0.3)  # let sh install its trap
    client.resize(33, 99)
    assert b"33 99" in _read_master_until_eof(client.master_fd)


def test_ptyclient_terminate_idempotent(spawn):
    """terminate() twice is safe and leaves the child dead and reaped."""
    client = spawn(["sleep", "30"])
    assert client.is_alive()
    client.terminate()
    client.terminate()
    assert not client.is_alive()
    with pytest.raises(OSError):
        client.write(b"x")


def test_ptyclient_write_and_wait(spawn):
    """write() reaches the child's stdin; wait() returns its exit status."""
    client = spawn(["sh", "-c", "read line; exit 3"])
    client.write(b"go\n")
    assert client.wait() == 3


def test_ptyclient_start_draining(spawn):
    """A drained headless client never blocks on a full pty buffer."""
    client = spawn(["sh", "-c", "yes | head -c 200000; exit 0"])
    client.start_draining()
    assert client.wait() == 0
    client.terminate()


# --- proxy ---


def test_proxy_holds_until_gate(spawn, pipes):
    """Output is held while the gate is unset, then flushed on gate.set()."""
    stdin_r, _stdin_w, stdout_r, stdout_w = pipes
    client = spawn(["sh", "-c", "echo hello; sleep 1"])
    gate = threading.Event()
    t = _ProxyThread(client, gate, hold_timeout=5, stdin_fd=stdin_r, stdout_fd=stdout_w)
    t.start()
    time.sleep(0.3)
    assert _pipe_is_empty(stdout_r)
    gate.set()
    assert b"hello" in _read_pipe_until(stdout_r, b"hello", 0.5)
    t.finish()


def test_proxy_releases_on_timeout(spawn, pipes):
    """Held output is flushed once hold_timeout elapses even if the gate never sets."""
    stdin_r, _stdin_w, stdout_r, stdout_w = pipes
    client = spawn(["sh", "-c", "echo hello; sleep 1"])
    gate = threading.Event()
    t = _ProxyThread(client, gate, hold_timeout=0.3, stdin_fd=stdin_r, stdout_fd=stdout_w)
    t.start()
    assert b"hello" in _read_pipe_until(stdout_r, b"hello", 3.0)
    t.finish()


def test_proxy_flushes_held_output_when_child_exits(spawn, pipes):
    """Output still held when the child exits is flushed before proxy returns."""
    stdin_r, _stdin_w, stdout_r, stdout_w = pipes
    client = spawn(["sh", "-c", "echo bye"])
    gate = threading.Event()
    t = _ProxyThread(client, gate, hold_timeout=60, stdin_fd=stdin_r, stdout_fd=stdout_w)
    t.start()
    t.finish()
    assert b"bye" in _read_pipe_until(stdout_r, b"bye", 1.0)
    assert not client.is_alive()


def test_proxy_forwards_held_input_after_release(spawn, pipes):
    """Input typed while held reaches the child after release."""
    stdin_r, stdin_w, stdout_r, stdout_w = pipes
    client = spawn(["cat"])
    gate = threading.Event()
    t = _ProxyThread(client, gate, hold_timeout=5, stdin_fd=stdin_r, stdout_fd=stdout_w)
    t.start()
    os.write(stdin_w, b"abc\n")
    time.sleep(0.3)
    assert _pipe_is_empty(stdout_r)
    gate.set()
    assert b"abc" in _read_pipe_until(stdout_r, b"abc", 2.0)
    os.close(stdin_w)
    t.finish()


def test_proxy_stdin_eof_terminates_child(spawn, pipes):
    """EOF on stdin terminates the child and proxy returns promptly."""
    stdin_r, stdin_w, _stdout_r, stdout_w = pipes
    client = spawn(["sleep", "30"])
    gate = threading.Event()
    gate.set()
    t = _ProxyThread(client, gate, hold_timeout=2.0, stdin_fd=stdin_r, stdout_fd=stdout_w)
    t.start()
    start = time.monotonic()
    os.close(stdin_w)
    t.finish(timeout=3.0)
    assert time.monotonic() - start < 3.0
    assert not client.is_alive()


def test_proxy_restores_termios_on_error(spawn, fds, pipes):
    """A failing stdout write propagates, but termios and SIGWINCH are restored."""
    _stdin_r, _stdin_w, _stdout_r, stdout_w = pipes
    tty_master, tty_slave = fds(*os.openpty())
    before = termios.tcgetattr(tty_slave)
    handler_before = signal.getsignal(signal.SIGWINCH)
    client = spawn(["sh", "-c", "echo hello; sleep 30"])
    gate = threading.Event()
    gate.set()
    real_write = os.write

    def failing_write(fd, data):
        if fd == stdout_w:
            raise OSError(errno.EIO, "stdout broke")
        return real_write(fd, data)

    with patch("scry.backends.pty_client.os.write", side_effect=failing_write):
        with pytest.raises(OSError, match="stdout broke"):
            proxy(client, gate, hold_timeout=2.0, stdin_fd=tty_slave, stdout_fd=stdout_w)

    assert termios.tcgetattr(tty_slave) == before
    assert signal.getsignal(signal.SIGWINCH) == handler_before
    assert not client.is_alive()


def test_proxy_propagates_sigwinch(spawn, fds, pipes):
    """SIGWINCH resizes the pty to stdin's terminal size (proxy on the main thread)."""
    _stdin_r, _stdin_w, stdout_r, stdout_w = pipes
    tty_master, tty_slave = fds(*os.openpty())
    _set_winsize(tty_slave, 50, 160)
    handler_before = signal.getsignal(signal.SIGWINCH)
    client = spawn(["sh", "-c", "sleep 0.5; stty size"])
    gate = threading.Event()
    gate.set()
    timer = threading.Timer(0.1, os.kill, args=(os.getpid(), signal.SIGWINCH))
    timer.start()
    try:
        proxy(client, gate, hold_timeout=2.0, stdin_fd=tty_slave, stdout_fd=stdout_w)
    finally:
        timer.cancel()
        timer.join()
    assert b"50 160" in _read_pipe_until(stdout_r, b"50 160", 1.0)
    assert signal.getsignal(signal.SIGWINCH) == handler_before
