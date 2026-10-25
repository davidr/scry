"""Validation of the remote-related config settings and quoting of remote commands.

Kept free of imports from scry.scry (which parses argv and builds a backend at import time).
"""

import logging
import os
import re
import shlex
from dataclasses import dataclass
from typing import Any, Dict, List

_LOGGER = logging.getLogger(__name__)

# Keys that are also bare commands at scry's prompt; a remote using one is shadowed there.
BARE_COMMANDS = set("sqzudlnr")


@dataclass(frozen=True)
class RemoteConfig:
    """One configured remote host."""

    key: str
    label: str
    host: str
    scry: str = "scry"


def parse_remotes(raw: Any) -> List[RemoteConfig]:
    """Validate the ``remotes`` config value.

    Args:
        raw: The raw config value; ``None`` means no remotes.

    Returns:
        List[RemoteConfig]: The validated remotes, in config order.

    Raises:
        ValueError: If the value is malformed; the message names the offending entry.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("remotes: must be a list")
    remotes: List[RemoteConfig] = []
    seen = set()
    for i, entry in enumerate(raw):
        name = f"remotes[{i}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{name}: must be a mapping")
        key = entry.get("key")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z]", key):
            raise ValueError(f"{name}: key must be a single ASCII letter, got {key!r}")
        if key in seen:
            raise ValueError(f"{name}: duplicate key {key!r}")
        seen.add(key)
        host = entry.get("host")
        if not isinstance(host, str) or not host or host.startswith("-"):
            raise ValueError(f"{name}: host must be a non-empty string not starting with '-', got {host!r}")
        label = entry.get("label", host)
        if not isinstance(label, str):
            raise ValueError(f"{name}: label must be a string")
        scry = entry.get("scry", "scry")
        if not isinstance(scry, str) or not (scry == "scry" or scry.startswith(("/", "~/"))):
            raise ValueError(f"{name}: scry must be 'scry' or a path starting with '/' or '~/', got {scry!r}")
        if key in BARE_COMMANDS:
            _LOGGER.warning("%s: key %r is also a scry command and will be shadowed at the prompt", name, key)
        remotes.append(RemoteConfig(key=key, label=label, host=host, scry=scry))
    return remotes


def remote_command(scry: str, args: List[str]) -> str:
    """Build the shell command line to run on the remote host.

    A leading ``~/`` is expanded by the remote shell via ``$HOME``; everything else is quoted.

    Args:
        scry: The remote scry executable (``scry``, an absolute path, or ``~/...``).
        args: Arguments to pass.

    Returns:
        str: The command line, safe to hand to the remote shell.
    """
    if scry.startswith("~/"):
        exe = '"$HOME"/' + shlex.quote(scry[2:])
    else:
        exe = shlex.quote(scry)
    return " ".join([exe] + [shlex.quote(a) for a in args])


def default_cache_dir() -> str:
    """Return the default remote cache directory.

    Returns:
        str: ``$XDG_CACHE_HOME/scry/remotes``, else ``~/.cache/scry/remotes``.
    """
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "scry", "remotes")


def validate_remote_settings(cfg: Dict) -> None:
    """Validate and normalize the remote settings of a config dict in place.

    Sets ``cfg["remotes"]`` to a ``List[RemoteConfig]`` and ``cfg["remote_cache_dir"]`` to an expanded path.

    Args:
        cfg: The scry config dict.

    Raises:
        ValueError: If any remote setting is invalid.
    """
    cfg["remotes"] = parse_remotes(cfg.get("remotes"))
    cache_dir = cfg.get("remote_cache_dir")
    cfg["remote_cache_dir"] = default_cache_dir() if cache_dir is None else os.path.expanduser(str(cache_dir))
    timeout = cfg.get("remote_timeout")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError(f"remote_timeout: must be a positive number, got {timeout!r}")
    if not isinstance(cfg.get("remote_ctrl_c_guard"), bool):
        raise ValueError(f"remote_ctrl_c_guard: must be true or false, got {cfg.get('remote_ctrl_c_guard')!r}")
