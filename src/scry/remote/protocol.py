"""Wire protocol shared by the remote server and its clients.

Responses are JSON framed between sentinel lines so that login-shell noise printed around them can be ignored.
"""

import json
from typing import Dict

PROTO_VERSION = 1
LIST_BEGIN = "<<<SCRY-REMOTE-LIST-BEGIN>>>"
LIST_END = "<<<SCRY-REMOTE-LIST-END>>>"
MAX_RESPONSE_BYTES = 1_000_000

EXIT_FAILURE = 80
EXIT_STALE = 81
EXIT_TAB_GONE = 82
EXIT_NO_SESSION = 83
EXIT_CONFIG = 84
EXIT_IDENTITY = 85
EXIT_PROTO = 86


class ProtocolError(Exception):
    """Raised when a remote response is missing, oversized or malformed."""


def frame(payload: Dict) -> str:
    """Frame a payload for output on the remote's stdout.

    Args:
        payload: JSON-serializable dict to send.

    Returns:
        str: The payload between the begin and end sentinel lines.
    """
    return f"{LIST_BEGIN}\n{json.dumps(payload)}\n{LIST_END}\n"


def unframe(stdout: bytes) -> Dict:
    """Extract the framed payload from remote stdout, ignoring surrounding noise.

    Args:
        stdout: Raw bytes received from the remote command.

    Returns:
        Dict: The decoded payload.

    Raises:
        ProtocolError: If the output is too large, has no complete frame, or the frame is not a JSON object.
    """
    if len(stdout) > MAX_RESPONSE_BYTES:
        raise ProtocolError("response too large")
    lines = stdout.decode("utf-8", errors="replace").splitlines()
    begin = next((i for i, line in enumerate(lines) if line.strip() == LIST_BEGIN), None)
    if begin is None:
        raise ProtocolError("no framed response")
    end = next((i for i in range(begin + 1, len(lines)) if lines[i].strip() == LIST_END), None)
    if end is None:
        raise ProtocolError("no framed response")
    try:
        data = json.loads("\n".join(lines[begin + 1 : end]))
    except ValueError as e:
        raise ProtocolError(f"invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ProtocolError("invalid JSON: payload is not an object")
    return data
