# -*- coding: utf-8 -*-
"""Pseudo-terminal client: run a command on a pty scry owns, and proxy a terminal to it.

`PtyClient` spawns a command as a session leader on a fresh pty (the pty is its
controlling terminal). `proxy` connects scry's own terminal to such a client, holding
both directions until a gate is set so that the first frames can be hidden.
"""

import errno
import fcntl
import os
import select
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
import tty
from typing import List, Optional

_READ_SIZE = 65536
""" int: maximum bytes per read from a pty or stdin
"""

_SELECT_TIMEOUT = 0.05
""" float: select() timeout in seconds; bounds how late the hold deadline is noticed
"""


_EXEC_FAILED = 127
""" int: exit status of the child when argv[0] cannot be executed (shell convention)
"""

_TRAMPOLINE = (
    "import fcntl, os, sys, termios\n"
    "fcntl.ioctl(0, termios.TIOCSCTTY, 0)\n"
    "try:\n"
    "    os.execvp(sys.argv[1], sys.argv[1:])\n"
    "except OSError as e:\n"
    "    sys.stderr.write(f'scry: cannot execute {sys.argv[1]}: {e.strerror}\\r\\n')\n"
    f"    sys.exit({_EXEC_FAILED})\n"
)
""" str: Python source run in the child (after setsid) to make the pty slave its
controlling terminal, so that pty resizes deliver SIGWINCH, then exec argv. Used
instead of `preexec_fn`, which is unsafe when other threads exist at fork time.
"""


def _read_or_eof(fd: int) -> bytes:
    """Read from an fd, treating EIO (pty slave side closed) as EOF.

    Args:
        fd: File descriptor to read.

    Returns:
        The bytes read; b"" on EOF.
    """
    try:
        return os.read(fd, _READ_SIZE)
    except OSError as e:
        if e.errno == errno.EIO:
            return b""
        raise


def _write_all(fd: int, data: bytes) -> None:
    """Write all of data to fd, looping over partial writes.

    Args:
        fd: File descriptor to write.
        data: Bytes to write.
    """
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


class PtyClient(object):
    """A command running on a pseudo-terminal owned by scry.

    Attributes:
        master_fd: The pty master; read for the child's output, write for its input.
        pid: The child's process ID (also its session and process group ID).
    """

    def __init__(self, argv: List[str], rows: int, cols: int):
        """Spawn argv as a session leader on a new pty of the given size.

        Args:
            argv: Command and arguments to run.
            rows: Initial pty height.
            cols: Initial pty width.

        If argv[0] cannot be executed, the constructor still succeeds: the child
        prints "scry: cannot execute ..." to the pty and exits with status 127, so
        callers observe `wait() == 127` (and pty EOF), not an exception or a hang.

        Raises:
            OSError: If the pty cannot be created or the Python trampoline cannot start.
        """
        self.master_fd, slave_fd = os.openpty()
        self._closed = False
        self._lock = threading.Lock()
        self._drain_stop = threading.Event()
        self._drain_thread: Optional[threading.Thread] = None
        try:
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            self._proc = subprocess.Popen(
                [sys.executable, "-I", "-c", _TRAMPOLINE, *argv],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
            )
        except BaseException:
            os.close(self.master_fd)
            self._closed = True
            raise
        finally:
            os.close(slave_fd)
        self.pid: int = self._proc.pid

    def write(self, data: bytes) -> None:
        """Write bytes to the child's input. Safe to call from any thread.

        Args:
            data: Bytes to send.

        Raises:
            OSError: If the client has been terminated (EBADF) or the write fails.
        """
        with self._lock:
            if self._closed:
                raise OSError(errno.EBADF, "pty client is closed")
            _write_all(self.master_fd, data)

    def resize(self, rows: int, cols: int) -> None:
        """Set the pty size (the child receives SIGWINCH).

        Args:
            rows: New height.
            cols: New width.

        Raises:
            OSError: If the client has been terminated (EBADF) or the ioctl fails.
        """
        # No lock: this runs from proxy's SIGWINCH handler on the main thread, which
        # may already hold self._lock inside write().
        if self._closed:
            raise OSError(errno.EBADF, "pty client is closed")
        fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def is_alive(self) -> bool:
        """Return True if the child has not exited."""
        return self._proc.poll() is None

    def wait(self) -> int:
        """Wait for the child to exit.

        Returns:
            The child's exit status (negative signal number if killed by a signal).
        """
        return self._proc.wait()

    def terminate(self, timeout: float = 2.0) -> None:
        """Stop the child and release the pty. Idempotent.

        Sends SIGHUP to the child's process group, waits up to timeout, then sends
        SIGKILL. The child is always reaped and the master fd closed.

        Args:
            timeout: Seconds to wait after SIGHUP before SIGKILL.
        """
        if self._proc.poll() is None:
            self._signal_group(signal.SIGHUP)
            try:
                self._proc.wait(timeout)
            except subprocess.TimeoutExpired:
                self._signal_group(signal.SIGKILL)
                self._proc.wait()
        self._drain_stop.set()
        if self._drain_thread is not None and self._drain_thread is not threading.current_thread():
            self._drain_thread.join()
        with self._lock:
            if not self._closed:
                self._closed = True
                os.close(self.master_fd)

    def start_draining(self) -> None:
        """Read and discard the child's output in a daemon thread (headless clients).

        Calling it again while a drain thread exists does nothing.
        """
        if self._drain_thread is not None:
            return
        self._drain_thread = threading.Thread(target=self._drain, name=f"pty-drain-{self.pid}", daemon=True)
        self._drain_thread.start()

    def _drain(self) -> None:
        """Discard pty output until EOF or terminate() asks the thread to stop."""
        while not self._drain_stop.is_set():
            ready, _, _ = select.select([self.master_fd], [], [], _SELECT_TIMEOUT)
            if ready and not _read_or_eof(self.master_fd):
                return

    def _signal_group(self, sig: int) -> None:
        """Send sig to the child's process group (unreaped, so the pgid is still ours)."""
        try:
            os.killpg(self.pid, sig)
        except ProcessLookupError:
            pass


def proxy(
    client: PtyClient,
    gate: threading.Event,
    hold_timeout: float = 2.0,
    stdin_fd: int = 0,
    stdout_fd: int = 1,
) -> None:
    """Connect a terminal to a PtyClient until the child exits.

    Copies stdin to the pty and pty output to stdout. Until `gate` is set or
    `hold_timeout` elapses, both directions are buffered; on release the held output
    is flushed in one write, then the held input is forwarded. If stdin is a tty it is
    put in raw mode, the pty is resized to match it, and SIGWINCH keeps it matched.
    On every exit path (child exit, stdin EOF, exception) the SIGWINCH handler and
    termios settings are restored first, then the client is terminated (reaped,
    master closed); each step runs even if an earlier one raises.

    Must run on the main thread when stdin_fd is a tty (it installs a signal handler).
    `client.write` may be called from other threads while this runs.

    Args:
        client: The client to connect to.
        gate: Released (set) by the caller when output may be shown.
        hold_timeout: Seconds after which held data is released even if gate is unset.
        stdin_fd: Terminal input fd.
        stdout_fd: Terminal output fd.
    """
    is_tty = os.isatty(stdin_fd)
    saved_attrs = None
    handler_installed = False
    prev_handler = None
    try:
        if is_tty:
            saved_attrs = termios.tcgetattr(stdin_fd)

            def _on_winch(_signum, _frame):
                # Never raise: an exception here would surface wherever the main
                # thread happens to be, possibly inside proxy's cleanup.
                try:
                    winsize = fcntl.ioctl(stdin_fd, termios.TIOCGWINSZ, b"\0" * 8)
                    rows, cols = struct.unpack("HHHH", winsize)[:2]
                    client.resize(rows, cols)
                except OSError:
                    pass

            prev_handler = signal.signal(signal.SIGWINCH, _on_winch)
            handler_installed = True
            _on_winch(signal.SIGWINCH, None)
            tty.setraw(stdin_fd, termios.TCSADRAIN)
        _proxy_loop(client, gate, hold_timeout, stdin_fd, stdout_fd)
    finally:
        try:
            if handler_installed:
                signal.signal(signal.SIGWINCH, prev_handler if prev_handler is not None else signal.SIG_DFL)
        finally:
            try:
                if saved_attrs is not None:
                    termios.tcsetattr(stdin_fd, termios.TCSADRAIN, saved_attrs)
            finally:
                client.terminate()


def _proxy_loop(client: PtyClient, gate: threading.Event, hold_timeout: float, stdin_fd: int, stdout_fd: int) -> None:
    """Copy bytes between the terminal and the client; see `proxy`.

    Returns when the pty reaches EOF (child side closed; all output drained and
    flushed) or stdin reaches EOF (held output flushed). `proxy` terminates the client.
    """
    master_fd = client.master_fd
    deadline = time.monotonic() + hold_timeout
    held = True
    held_out = bytearray()
    held_in = bytearray()

    while True:
        if held and (gate.is_set() or time.monotonic() >= deadline):
            held = False
            if held_out:
                _write_all(stdout_fd, held_out)
                held_out.clear()
            if held_in:
                client.write(bytes(held_in))
                held_in.clear()

        readable, _, _ = select.select([master_fd, stdin_fd], [], [], _SELECT_TIMEOUT)

        if master_fd in readable:
            data = _read_or_eof(master_fd)
            if not data:
                break
            if held:
                held_out += data
            else:
                _write_all(stdout_fd, data)

        if stdin_fd in readable:
            data = _read_or_eof(stdin_fd)
            if not data:
                break
            if held:
                held_in += data
            else:
                client.write(data)

    if held_out:
        _write_all(stdout_fd, held_out)
