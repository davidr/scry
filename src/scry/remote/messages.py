"""User-facing messages for remote exit codes, shared by refresh and attach.

Kept free of imports from scry.scry.
"""

import signal

from scry.remote.protocol import (
    EXIT_CONFIG,
    EXIT_IDENTITY,
    EXIT_NO_SESSION,
    EXIT_PROTO,
    EXIT_STALE,
    EXIT_TAB_GONE,
)


def exit_message(code: int, key: str) -> str:
    """Describe a remote command's exit code for the prompt slot.

    Args:
        code: The process return code (negative for death by signal).
        key: The remote's key, used to name the host in the message.

    Returns:
        str: The message, or an empty string for exit code 0.
    """
    if code == 0:
        return ""
    messages = {
        255: f"host {key} unavailable, or the connection was closed",
        127: f"scry not found on host {key}; set `scry:` in remotes",
        2: f"scry on host {key} is too old for remote mode",
        EXIT_STALE: f"host {key}'s tab list is stale — press z",
        EXIT_TAB_GONE: f"that tab is gone on host {key} — press z",
        EXIT_NO_SESSION: f"no zellij session running on host {key}",
        EXIT_CONFIG: (
            f"scry on host {key} isn't set up for remote mode "
            "(needs backend: zellij and zellij_wake_keys in its ~/.scry.yml)"
        ),
        EXIT_IDENTITY: f"couldn't identify the zellij session on host {key}",
        EXIT_PROTO: f"scry on host {key} is incompatible — upgrade it",
        -signal.SIGINT: f"connection to host {key} cancelled",
    }
    return messages.get(code, f"remote command on host {key} failed (exit {code})")
