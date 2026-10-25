"""Attach to a remote zellij tab over ssh."""

import os
import signal
import subprocess
import termios
from contextlib import contextmanager
from typing import Iterator, List

from rich.console import Console
from rich.text import Text

from scry.remote.cache import RemoteTabRef
from scry.remote.config import RemoteConfig, remote_command
from scry.remote.messages import exit_message
from scry.remote.protocol import PROTO_VERSION

# Leave the alternate screen, show the cursor, disable mouse/focus/bracketed-paste modes, pop kitty keyboard mode
RESET_SEQUENCE = "\x1b[?1049l\x1b[?25h\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l\x1b[?1004l\x1b[?2004l\x1b[<u"


def attach_command(remote: RemoteConfig, ref: RemoteTabRef) -> List[str]:
    """Build the ssh argv that attaches to a remote tab.

    Args:
        remote: The remote to connect to.
        ref: The remote tab to attach to.

    Returns:
        List[str]: The ssh command line.
    """
    args = ["remote-attach", f"--tab={ref.tab_id}", f"--expect-session={ref.identity}", f"--proto={PROTO_VERSION}"]
    return ["ssh", "-t", remote.host, remote_command(remote.scry, args)]


def _noop(signum, frame) -> None:
    """Swallow SIGINT in scry itself."""


@contextmanager
def sigint_guard(enabled: bool) -> Iterator[None]:
    """Keep Ctrl-C from killing scry while an ssh child runs.

    Installs a no-op Python handler rather than ``SIG_IGN``: an ignored disposition is inherited
    across exec, which would make ssh ignore Ctrl-C too. A handler is reset to default in the child.

    Args:
        enabled: When False, nothing is installed.

    Yields:
        None.
    """
    if not enabled:
        yield
        return
    prev = signal.signal(signal.SIGINT, _noop)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, prev)


@contextmanager
def restored_terminal(stdin_fd: int = 0, stdout_fd: int = 1) -> Iterator[None]:
    """Restore terminal modes and emit a reset sequence afterwards, whatever happens.

    Does nothing when stdin is not a tty.

    Args:
        stdin_fd: The terminal input fd whose attributes are saved and restored.
        stdout_fd: The fd the reset sequence is written to.

    Yields:
        None.
    """
    if not os.isatty(stdin_fd):
        yield
        return
    saved = termios.tcgetattr(stdin_fd)
    try:
        yield
    finally:
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, saved)
        os.write(stdout_fd, RESET_SEQUENCE.encode())


def attach_remote(ref: RemoteTabRef, remotes: List[RemoteConfig], guard: bool, console: Console) -> str:
    """Attach to a remote tab over ssh and wait until the session ends.

    Args:
        ref: The remote tab to attach to.
        remotes: The configured remotes.
        guard: Whether the Ctrl-C guard is enabled.
        console: The Rich console.

    Returns:
        str: An error message, or "" on success.
    """
    remote = next((r for r in remotes if r.host == ref.host), None)
    if remote is None:
        return f"no remote configured for host {ref.host}"
    console.print(Text(f"connecting to host {remote.key}…"))
    try:
        with restored_terminal(), sigint_guard(guard):
            rc = subprocess.call(attach_command(remote, ref))
    except OSError as e:
        return f"cannot run ssh: {e.strerror}"
    return exit_message(rc, remote.key)
