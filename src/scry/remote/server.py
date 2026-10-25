"""Headless server side of remote zellij tabs: the `remote-list` and `remote-attach` subcommands.

Run on the remote host via ssh by the local scry. Must never import scry.scry (it parses argv and builds a backend at
import time) and never enters the interactive loop or creates sessions.
"""

import argparse
import logging
import signal
import sys
from typing import Dict, List, Optional, TextIO, Tuple

import yaml

from scry.backends.zellij import ZellijBackend
from scry.config_file import CONFIG_PATH, read_config_file
from scry.remote.identity import IdentityError, find_session_identity
from scry.remote.protocol import (
    EXIT_CONFIG,
    EXIT_FAILURE,
    EXIT_IDENTITY,
    EXIT_NO_SESSION,
    EXIT_PROTO,
    EXIT_STALE,
    EXIT_TAB_GONE,
    PROTO_VERSION,
    frame,
)

# Mirrors default_config["session_group"] in scry.scry (which cannot be imported here).
DEFAULT_SESSION_GROUP = "main"
DEFAULT_LOG_FILE = "/tmp/scry.log"


def load_remote_config(path: str = CONFIG_PATH) -> Tuple[Dict, Optional[str]]:
    """Read and validate the remote config file.

    Args:
        path: Config file path.

    Returns:
        Tuple[Dict, Optional[str]]: The config (with session_group defaulted) and an error message, or None if valid.
    """
    try:
        cfg = read_config_file(path)
    except (yaml.YAMLError, OSError) as e:
        return {"session_group": DEFAULT_SESSION_GROUP}, f"cannot read remote config: {type(e).__name__}"
    if not isinstance(cfg, dict):
        return {"session_group": DEFAULT_SESSION_GROUP}, "remote config is not a mapping"
    error = None
    if cfg.get("backend") != "zellij":
        error = "remote backend must be zellij"
    elif "zellij_wake_keys" not in cfg:
        error = "zellij_wake_keys must be set in the remote ~/.scry.yml"
    cfg = dict(cfg)
    cfg.setdefault("session_group", DEFAULT_SESSION_GROUP)
    return cfg, error


def _setup_logging(cfg: Dict) -> None:
    """Route logging to the debug log file, or nowhere; never to stderr."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    if cfg.get("debug"):
        root.addHandler(logging.FileHandler(cfg.get("log_file", DEFAULT_LOG_FILE)))
        root.setLevel(logging.DEBUG)
    else:
        root.addHandler(logging.NullHandler())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scry")
    sub = parser.add_subparsers(dest="command", required=True)
    list_p = sub.add_parser("remote-list")
    list_p.add_argument("--proto", type=int, required=True)
    attach_p = sub.add_parser("remote-attach")
    attach_p.add_argument("--tab", type=str, required=True)
    attach_p.add_argument("--expect-session", type=str, required=True)
    attach_p.add_argument("--proto", type=int, required=True)
    return parser


def _error(out: TextIO, message: str, code: int) -> int:
    out.write(frame({"proto": PROTO_VERSION, "status": "error", "error": message}))
    return code


def _exit_on_sigint(signum, frame_) -> None:
    raise SystemExit(EXIT_FAILURE)


def _remote_list(cfg: Dict, backend: ZellijBackend, out: TextIO, proc_root: str) -> int:
    session = cfg["session_group"]
    if session not in [s["session_name"] for s in backend.list_sessions()]:
        out.write(frame({"proto": PROTO_VERSION, "status": "no_session", "session": session}))
        return 0
    try:
        identity = find_session_identity(session, proc_root)
    except IdentityError as e:
        return _error(out, str(e), EXIT_IDENTITY)
    tabs = [
        {"id": int(t["tab_id"]), "name": str(t["name"]), "clients": len(t.get("other_focused_clients", []))}
        for t in backend._list_tabs(session)
    ]
    out.write(
        frame(
            {
                "proto": PROTO_VERSION,
                "status": "ok",
                "session": session,
                "identity": identity.token,
                "started_at": str(identity.started_at),
                "tabs": tabs,
            }
        )
    )
    return 0


def _remote_attach(cfg: Dict, backend: ZellijBackend, args: argparse.Namespace, proc_root: str) -> int:
    session = cfg["session_group"]
    if session not in [s["session_name"] for s in backend.list_sessions()]:
        return EXIT_NO_SESSION
    try:
        identity = find_session_identity(session, proc_root)
    except IdentityError:
        return EXIT_IDENTITY
    if identity.token != args.expect_session:
        return EXIT_STALE
    if not any(str(t["tab_id"]) == args.tab for t in backend._list_tabs(session)):
        return EXIT_TAB_GONE
    try:
        backend.attach_window(args.tab, session)
    except RuntimeError as e:
        return EXIT_TAB_GONE if "no longer exists" in str(e) else EXIT_FAILURE
    return 0


def main(argv: List[str], proc_root: str = "/proc", out: TextIO = sys.stdout) -> int:
    """Run a remote-* subcommand.

    Args:
        argv: Arguments after the program name, starting with the subcommand.
        proc_root: Root of the proc filesystem (overridable for tests).
        out: Stream receiving the framed response (remote-list only).

    Returns:
        int: The process exit code (see scry.remote.protocol).

    Raises:
        SystemExit: With code 2 on missing or unknown arguments (the local side treats this as "remote too old").
    """
    args = _parser().parse_args(argv)
    is_attach = args.command == "remote-attach"
    if is_attach:
        signal.signal(signal.SIGINT, _exit_on_sigint)

    _setup_logging({})
    cfg, config_error = load_remote_config()
    _setup_logging(cfg)

    def fail(message: str, code: int) -> int:
        # remote-attach prints nothing: the ssh -t output would be wiped by the local redraw.
        return code if is_attach else _error(out, message, code)

    if args.proto != PROTO_VERSION:
        return fail("protocol mismatch", EXIT_PROTO)
    if config_error:
        return fail(config_error, EXIT_CONFIG)
    try:
        backend = ZellijBackend(wake_keys=cfg["zellij_wake_keys"], zellij_bin=cfg.get("zellij_bin"))
    except ValueError as e:
        return fail(str(e), EXIT_CONFIG)

    try:
        if is_attach:
            return _remote_attach(cfg, backend, args, proc_root)
        return _remote_list(cfg, backend, out, proc_root)
    except Exception as e:  # noqa: BLE001 - report any failure to the caller as a protocol error
        logging.getLogger(__name__).exception("remote command failed")
        return fail(f"{type(e).__name__}: {e}", EXIT_FAILURE)
