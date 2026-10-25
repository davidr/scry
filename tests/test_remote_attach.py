"""Tests for the remote ssh attach, Ctrl-C guard and terminal restore."""

import io
import os
import signal
import subprocess
from unittest import mock

import pytest
from rich.console import Console

from scry.remote.attach import RESET_SEQUENCE, attach_command, attach_remote, restored_terminal, sigint_guard
from scry.remote.cache import RemoteSection, RemoteTabRef
from scry.remote.config import RemoteConfig
from scry.remote.messages import exit_message

B = RemoteConfig(key="b", label="Build", host="coder.build")
REF = RemoteTabRef("coder.build", "101:12345", "5")


def _console():
    return Console(file=io.StringIO(), width=120, highlight=False)


def test_attach_command_argv():
    assert attach_command(B, REF) == [
        "ssh",
        "-t",
        "coder.build",
        "scry remote-attach --tab=5 --expect-session=101:12345 --proto=1",
    ]


def test_exit_message_mapping():
    key = "b"
    assert exit_message(0, key) == ""
    assert exit_message(255, key) == "host b unavailable, or the connection was closed"
    assert exit_message(127, key) == "scry not found on host b; set `scry:` in remotes"
    assert exit_message(2, key) == "scry on host b is too old for remote mode"
    assert exit_message(81, key) == "host b's tab list is stale — press z"
    assert exit_message(82, key) == "that tab is gone on host b — press z"
    assert exit_message(83, key) == "no zellij session running on host b"
    assert exit_message(84, key).startswith("scry on host b isn't set up for remote mode")
    assert exit_message(85, key) == "couldn't identify the zellij session on host b"
    assert exit_message(86, key) == "scry on host b is incompatible — upgrade it"
    assert exit_message(-signal.SIGINT, key) == "connection to host b cancelled"
    assert exit_message(3, key) == "remote command on host b failed (exit 3)"


@pytest.fixture
def restore_sigint():
    prev = signal.getsignal(signal.SIGINT)
    yield
    signal.signal(signal.SIGINT, prev)


def test_sigint_guard_child_still_dies_and_scry_survives(restore_sigint):
    with sigint_guard(True):
        p = subprocess.Popen(["sleep", "30"])
        try:
            os.kill(p.pid, signal.SIGINT)
            os.kill(os.getpid(), signal.SIGINT)
            assert p.wait(5) == -signal.SIGINT
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()


def test_sig_ign_would_break_escape(restore_sigint):
    """SIG_IGN is inherited across exec, so the child would survive Ctrl-C: why the guard uses a no-op handler."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    p = subprocess.Popen(["sleep", "30"])
    try:
        os.kill(p.pid, signal.SIGINT)
        with pytest.raises(subprocess.TimeoutExpired):
            p.wait(0.5)
        assert p.poll() is None
    finally:
        p.kill()
        p.wait()


def test_sigint_guard_restores_previous_handler(restore_sigint):
    def prev(signum, frame):
        pass

    signal.signal(signal.SIGINT, prev)
    with sigint_guard(True):
        assert signal.getsignal(signal.SIGINT) is not prev
    assert signal.getsignal(signal.SIGINT) is prev
    with pytest.raises(RuntimeError):
        with sigint_guard(True):
            raise RuntimeError("boom")
    assert signal.getsignal(signal.SIGINT) is prev


def test_sigint_guard_disabled_installs_nothing(restore_sigint):
    before = signal.getsignal(signal.SIGINT)
    with sigint_guard(False):
        assert signal.getsignal(signal.SIGINT) is before
    assert signal.getsignal(signal.SIGINT) is before


@pytest.mark.parametrize("rc", [0, 255, "raise"])
def test_restored_terminal_on_success_failure_and_exception(rc):
    saved = ["saved-attrs"]
    with (
        mock.patch("scry.remote.attach.os.isatty", return_value=True),
        mock.patch("scry.remote.attach.termios.tcgetattr", return_value=saved),
        mock.patch("scry.remote.attach.termios.tcsetattr") as tcset,
        mock.patch("scry.remote.attach.os.write") as write,
        mock.patch("scry.remote.attach.subprocess.call") as call,
    ):
        if rc == "raise":
            call.side_effect = OSError(2, "No such file or directory")
        else:
            call.return_value = rc
        attach_remote(REF, [B], False, _console())
    tcset.assert_called_once()
    assert tcset.call_args.args[2] is saved
    write.assert_called_once_with(1, RESET_SEQUENCE.encode())


def test_restored_terminal_restores_when_body_raises():
    saved = ["saved-attrs"]
    with (
        mock.patch("scry.remote.attach.os.isatty", return_value=True),
        mock.patch("scry.remote.attach.termios.tcgetattr", return_value=saved),
        mock.patch("scry.remote.attach.termios.tcsetattr") as tcset,
        mock.patch("scry.remote.attach.os.write") as write,
    ):
        with pytest.raises(RuntimeError):
            with restored_terminal():
                raise RuntimeError("boom")
    tcset.assert_called_once()
    write.assert_called_once_with(1, RESET_SEQUENCE.encode())


def test_restored_terminal_not_a_tty_is_noop():
    with (
        mock.patch("scry.remote.attach.os.isatty", return_value=False),
        mock.patch("scry.remote.attach.termios.tcsetattr") as tcset,
        mock.patch("scry.remote.attach.os.write") as write,
    ):
        with restored_terminal():
            pass
    tcset.assert_not_called()
    write.assert_not_called()


def test_attach_remote_prints_connecting_and_maps_rc():
    console = _console()
    with mock.patch("scry.remote.attach.subprocess.call", return_value=81):
        msg = attach_remote(REF, [B], False, console)
    assert "connecting to host b…" in console.file.getvalue()
    assert msg == exit_message(81, "b")


def test_attach_remote_unknown_host_and_missing_ssh():
    assert attach_remote(REF, [], False, _console()) == "no remote configured for host coder.build"
    with mock.patch("scry.remote.attach.subprocess.call", side_effect=OSError(2, "No such file or directory")):
        assert attach_remote(REF, [B], False, _console()) == "cannot run ssh: No such file or directory"


def test_attach_remote_guard_off_with_config():
    with (
        mock.patch("scry.remote.attach.subprocess.call", return_value=0),
        mock.patch("scry.remote.attach.signal.signal") as sig,
    ):
        assert attach_remote(REF, [B], False, _console()) == ""
    sig.assert_not_called()


class _StopLoop(Exception):
    pass


def _run_loop(result, clean_window_history):
    from scry.scry import WINDOW_HISTORY, do_table_loop

    section = RemoteSection(
        remote=B,
        state="ok",
        last_success=1.0,
        reason="",
        rows=[{"window_id": REF, "window_name": "x", "window_active_clients": ""}],
    )
    with (
        mock.patch("scry.scry.backend") as be,
        mock.patch("scry.scry.setup_display"),
        mock.patch("scry.scry.load_sections", return_value=[section]),
        mock.patch("scry.scry.attach_remote", return_value=result) as att,
        mock.patch("scry.scry.Prompt.ask", side_effect=["b0", _StopLoop()]),
    ):
        be.list_windows.return_value = [{"window_id": "@1", "window_name": "w", "window_active_clients": ""}]
        with pytest.raises(_StopLoop):
            do_table_loop()
    assert att.call_args.args[0] == REF
    return WINDOW_HISTORY


def test_attach_remote_success_updates_history(clean_window_history):
    assert REF in _run_loop("", clean_window_history)


def test_attach_remote_failure_leaves_history(clean_window_history):
    assert REF not in _run_loop("boom", clean_window_history)
