"""Per-host cache files and the remote section model.

The index redraw calls ``load_sections`` on every loop iteration, so everything it uses here is
local-file-only and must never raise. Kept free of imports from scry.scry.
"""

import json
import os
import re
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, List, NamedTuple, Optional

from scry.remote.config import RemoteConfig


class RemoteTabRef(NamedTuple):
    """Identity of a remote tab: the history entry and the ``window_id`` of remote rows."""

    host: str
    identity: str
    tab_id: str


@dataclass
class RemoteSection:
    """One remote host's section of the index.

    ``state`` is one of ``"ok"``, ``"failed"``, ``"no_session"`` or ``"never"``. Each row is
    ``{"window_id": RemoteTabRef, "window_name": str, "window_active_clients": str}``.
    """

    remote: RemoteConfig
    state: str
    last_success: Optional[float]
    reason: str
    rows: List[Dict[str, Any]]


@dataclass
class RefreshResult:
    """Outcome of one remote refresh attempt.

    ``payload`` is the framed list payload for ``ok``; it may be None otherwise.
    """

    status: str
    reason: str = ""
    payload: Optional[Dict[str, Any]] = None


def cache_path(cache_dir: str, host: str) -> str:
    """Return the cache file path for a host.

    Args:
        cache_dir: The cache directory.
        host: The remote host string; characters outside ``[A-Za-z0-9._-]`` become ``_``.

    Returns:
        str: The path of the host's JSON cache file.
    """
    return os.path.join(cache_dir, re.sub(r"[^A-Za-z0-9._-]", "_", host) + ".json")


def read_cache(cache_dir: str, host: str) -> Optional[Dict[str, Any]]:
    """Read a host's cache file.

    Args:
        cache_dir: The cache directory.
        host: The remote host string.

    Returns:
        Optional[Dict[str, Any]]: The cached dict, or None if the file is missing, empty,
        unreadable, not JSON, or not a JSON object. Never raises.
    """
    try:
        with open(cache_path(cache_dir, host), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_cache(cache_dir: str, host: str, data: Dict[str, Any]) -> None:
    """Atomically write a host's cache file.

    Args:
        cache_dir: The cache directory; created if missing.
        host: The remote host string.
        data: The JSON-serializable dict to store.

    Raises:
        OSError: If the directory or file cannot be written; no temp file is left behind.
    """
    os.makedirs(cache_dir, exist_ok=True)
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=cache_dir, prefix=".tmp-", delete=False, encoding="utf-8") as f:
            tmp_name = f.name
            json.dump(data, f)
            f.flush()
        os.replace(tmp_name, cache_path(cache_dir, host))
    except (OSError, TypeError, ValueError):
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        raise


def apply_result(old: Optional[Dict[str, Any]], host: str, result: RefreshResult, now: float) -> Dict[str, Any]:
    """Merge a refresh result into the previous cache contents.

    Args:
        old: The previous cache dict, or None.
        host: The remote host string.
        result: The refresh outcome.
        now: The current time (epoch seconds).

    Returns:
        Dict[str, Any]: The new cache dict. ``ok`` replaces the session data and sets
        ``last_success``; ``no_session`` clears tabs and identity; ``failed`` changes only
        ``last_attempt``.
    """
    new: Dict[str, Any] = {
        "host": host,
        "session": None,
        "identity": None,
        "started_at": None,
        "tabs": [],
        "last_success": None,
    }
    if old:
        new.update({k: old[k] for k in new if k in old})
    new["host"] = host
    if result.status == "ok":
        payload = result.payload or {}
        new["session"] = payload.get("session")
        new["identity"] = payload.get("identity")
        new["started_at"] = payload.get("started_at")
        new["tabs"] = list(payload.get("tabs", []))
        new["last_success"] = now
    elif result.status == "no_session":
        new["identity"] = None
        new["started_at"] = None
        new["tabs"] = []
    new["last_attempt"] = {"status": result.status, "reason": result.reason, "at": now}
    return new


def _rows(host: str, cache: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build sorted display rows from a cache dict, skipping malformed tabs."""
    identity = cache.get("identity")
    tabs = cache.get("tabs")
    if not identity or not isinstance(tabs, list):
        return []
    entries = []
    for tab in tabs:
        try:
            tab_id = int(tab["id"])
            name = str(tab["name"])
            clients = int(tab.get("clients", 0))
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            continue
        entries.append((name, tab_id, clients))
    entries.sort(key=lambda e: (e[0], e[1]))
    return [
        {
            "window_id": RemoteTabRef(host, str(identity), str(tab_id)),
            "window_name": name,
            "window_active_clients": str(clients),
        }
        for name, tab_id, clients in entries
    ]


def load_sections(remotes: List[RemoteConfig], cache_dir: str) -> List[RemoteSection]:
    """Build the remote sections from the cache files, in config order.

    Never touches the network and never raises on bad cache contents.

    Args:
        remotes: The configured remotes.
        cache_dir: The cache directory.

    Returns:
        List[RemoteSection]: One section per remote; ``state`` is ``"never"`` when no usable cache exists.
    """
    sections = []
    for remote in remotes:
        cache = read_cache(cache_dir, remote.host)
        if cache is None:
            sections.append(RemoteSection(remote, "never", None, "", []))
            continue
        attempt = cache.get("last_attempt")
        if not isinstance(attempt, dict):
            attempt = {}
        last_success = cache.get("last_success")
        if not isinstance(last_success, (int, float)) or isinstance(last_success, bool):
            last_success = None
        state = attempt.get("status")
        if state not in ("ok", "failed", "no_session"):
            state = "ok" if last_success is not None else "never"
        reason = attempt.get("reason")
        sections.append(
            RemoteSection(
                remote, state, last_success, reason if isinstance(reason, str) else "", _rows(remote.host, cache)
            )
        )
    return sections
