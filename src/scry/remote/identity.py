"""Identify a zellij session by its server process, using /proc (Linux only)."""

import os
import time
from dataclasses import dataclass


class IdentityError(Exception):
    """Raised when a session's server process cannot be uniquely identified."""


@dataclass(frozen=True)
class SessionIdentity:
    """Identity of a zellij server process.

    Attributes:
        pid: Server process id.
        starttime: Process start time in clock ticks since boot (/proc/<pid>/stat field 22).
        started_at: Human-readable start time, for diagnostics only ("unknown" if unavailable).
    """

    pid: int
    starttime: int
    started_at: str

    @property
    def token(self) -> str:
        """Opaque identity string that changes if the session is restarted."""
        return f"{self.pid}:{self.starttime}"


def _read_starttime(proc_root: str, pid: int) -> int:
    """Read a process's start time from its stat file.

    Args:
        proc_root: Root of the proc filesystem.
        pid: Process id.

    Returns:
        int: Start time in clock ticks.

    Raises:
        IdentityError: If the stat file cannot be read or parsed.
    """
    try:
        with open(os.path.join(proc_root, str(pid), "stat")) as f:
            data = f.read()
        rest = data[data.rindex(")") + 2 :].split()
        return int(rest[19])
    except (OSError, ValueError, IndexError) as e:
        raise IdentityError(f"cannot read start time of pid {pid}: {e}") from e


def _format_started_at(proc_root: str, starttime: int) -> str:
    """Format a process start time as local wall-clock time.

    Args:
        proc_root: Root of the proc filesystem.
        starttime: Start time in clock ticks since boot.

    Returns:
        str: Formatted time, or "unknown" on any failure.
    """
    try:
        btime = None
        with open(os.path.join(proc_root, "stat")) as f:
            for line in f:
                if line.startswith("btime "):
                    btime = int(line.split()[1])
                    break
        if btime is None:
            return "unknown"
        t = btime + starttime / os.sysconf("SC_CLK_TCK")
        return time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(t))
    except (OSError, ValueError, IndexError):
        return "unknown"


def find_session_identity(session: str, proc_root: str = "/proc") -> SessionIdentity:
    """Find the zellij server process for a session.

    Matches processes whose argv is `<bin> --server <path>` with basename(path) == session.

    Args:
        session: Zellij session name.
        proc_root: Root of the proc filesystem (overridable for tests).

    Returns:
        SessionIdentity: Identity of the single matching server.

    Raises:
        IdentityError: If there is no match, more than one match, or the start time is unreadable.
    """
    pids = []
    try:
        entries = os.listdir(proc_root)
    except OSError as e:
        raise IdentityError(f"cannot read {proc_root}: {e}") from e
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(os.path.join(proc_root, entry, "cmdline"), "rb") as f:
                raw = f.read()
        except OSError:
            continue
        argv = raw.decode(errors="replace").split("\0")
        if argv and argv[-1] == "":
            argv.pop()
        if len(argv) >= 3 and argv[1] == "--server" and os.path.basename(argv[2]) == session:
            pids.append(int(entry))
    if not pids:
        raise IdentityError(f"no zellij server for session {session}")
    if len(pids) > 1:
        raise IdentityError(f"ambiguous: pids {', '.join(str(p) for p in sorted(pids))} serve session {session}")
    pid = pids[0]
    starttime = _read_starttime(proc_root, pid)
    return SessionIdentity(pid, starttime, _format_started_at(proc_root, starttime))
