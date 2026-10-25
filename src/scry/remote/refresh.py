"""Parallel refresh of all remote hosts for the ``z`` command.

Kept free of imports from scry.scry.
"""

import os
import signal
import subprocess
import threading
import time
from typing import Dict, List

from scry.remote.cache import RefreshResult, apply_result, read_cache, write_cache
from scry.remote.config import RemoteConfig, remote_command
from scry.remote.messages import exit_message
from scry.remote.protocol import EXIT_PROTO, PROTO_VERSION, ProtocolError, unframe

REAP_TIMEOUT = 2.0


def refresh_command(remote: RemoteConfig) -> List[str]:
    """Build the ssh argv that asks a remote scry for its tab list.

    Args:
        remote: The remote to query.

    Returns:
        List[str]: The argv for ``subprocess.Popen``.
    """
    return [
        "ssh",
        "-T",
        "-o",
        "BatchMode=yes",
        remote.host,
        remote_command(remote.scry, ["remote-list", f"--proto={PROTO_VERSION}"]),
    ]


def parse_refresh(remote: RemoteConfig, returncode: int, stdout: bytes, stderr: bytes) -> RefreshResult:
    """Turn a finished ``remote-list`` process into a refresh result.

    A framed payload wins over the exit code.

    Args:
        remote: The remote that was queried.
        returncode: The ssh process return code.
        stdout: Captured stdout.
        stderr: Captured stderr.

    Returns:
        RefreshResult: ``ok``, ``no_session`` or ``failed`` with a reason.
    """
    key = remote.key
    try:
        payload = unframe(stdout)
    except ProtocolError as e:
        if returncode in (255, 127, 2):
            reason = exit_message(returncode, key)
            if returncode == 255:
                text = stderr.decode("utf-8", errors="replace")
                if "Permission denied" in text or "Host key verification failed" in text:
                    reason += f"; run 'ssh {remote.host}' by hand once"
            return RefreshResult("failed", reason)
        return RefreshResult("failed", f"bad response from host {key}: {e} (exit {returncode})")
    if payload.get("proto") != PROTO_VERSION:
        return RefreshResult("failed", exit_message(EXIT_PROTO, key))
    status = payload.get("status")
    if status == "ok":
        return RefreshResult("ok", payload=payload)
    if status == "no_session":
        return RefreshResult("no_session")
    return RefreshResult("failed", str(payload.get("error", f"bad response from host {key}")))


def _kill_group(proc: "subprocess.Popen") -> None:
    """SIGKILL a process's whole group, ignoring one that is already gone."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _reap(proc: "subprocess.Popen") -> None:
    """Reap a killed process without ever blocking on a stray pipe holder."""
    try:
        proc.communicate(timeout=REAP_TIMEOUT)
    except subprocess.TimeoutExpired:
        for f in (proc.stdout, proc.stderr):
            if f:
                f.close()
        proc.kill()
        try:
            proc.wait(timeout=REAP_TIMEOUT)
        except subprocess.TimeoutExpired:
            pass


def _wait_workers(threads: List[threading.Thread]) -> None:
    """Join threads in short slices so SIGINT is delivered to the main thread."""
    for t in threads:
        while t.is_alive():
            t.join(0.1)


def refresh_remotes(remotes: List[RemoteConfig], cache_dir: str, timeout: float, guard: bool) -> str:
    """Refresh every remote in parallel and update the cache files.

    Args:
        remotes: The configured remotes.
        cache_dir: The cache directory.
        timeout: Seconds allowed per host, for the whole refresh.
        guard: Whether Ctrl-C cancels the refresh (True) or propagates (False).

    Returns:
        str: The message for the prompt slot: failures joined by ``; ``, ``"refresh cancelled"``,
        or an empty string.
    """
    if not remotes:
        return "no remotes configured"
    deadline = time.monotonic() + timeout
    results: Dict[str, RefreshResult] = {}
    procs: Dict[str, "subprocess.Popen"] = {}
    finished = set()

    def work(remote: RemoteConfig, proc: "subprocess.Popen") -> None:
        try:
            try:
                out, err = proc.communicate(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                _kill_group(proc)
                _reap(proc)
                results[remote.key] = RefreshResult("failed", f"timed out after {timeout:g}s")
            else:
                results[remote.key] = parse_refresh(remote, proc.returncode, out, err)
        except Exception as e:
            results[remote.key] = RefreshResult("failed", f"refresh error on host {remote.key}: {e}")
        finally:
            finished.add(remote.key)

    threads = []
    for remote in remotes:
        try:
            proc = subprocess.Popen(
                refresh_command(remote),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as e:
            results[remote.key] = RefreshResult("failed", f"cannot run ssh ({e.strerror or e})")
            continue
        procs[remote.key] = proc
        t = threading.Thread(target=work, args=(remote, proc), daemon=True)
        t.start()
        threads.append(t)

    if guard:
        try:
            _wait_workers(threads)
        except KeyboardInterrupt:
            for key, proc in procs.items():
                if key not in finished:
                    _kill_group(proc)
            for t in threads:
                t.join(REAP_TIMEOUT * 2)
            return "refresh cancelled"
    else:
        _wait_workers(threads)

    problems = []
    now = time.time()
    for remote in remotes:
        result = results[remote.key]
        if result.status == "failed":
            problems.append(f"{remote.key}: {result.reason}")
        try:
            old = read_cache(cache_dir, remote.host)
            write_cache(cache_dir, remote.host, apply_result(old, remote.host, result, now))
        except OSError as e:
            problems.append(f"{remote.key}: cannot write cache ({e.strerror or e})")
    return "; ".join(problems)
