"""Tests for the headless remote server subcommands."""

import io
import logging
import signal
from unittest.mock import MagicMock, patch

import pytest
import yaml

from scry.remote import server
from scry.remote.identity import IdentityError, SessionIdentity
from scry.remote.protocol import (
    EXIT_CONFIG,
    EXIT_IDENTITY,
    EXIT_NO_SESSION,
    EXIT_PROTO,
    EXIT_STALE,
    EXIT_TAB_GONE,
    unframe,
)

GOOD_CFG = {"backend": "zellij", "zellij_wake_keys": "\x07\x07"}
IDENT = SessionIdentity(pid=42, starttime=1234, started_at="2026-10-25 10:00:00 UTC")
TABS = [
    {"tab_id": 5, "name": "alpha", "other_focused_clients": []},
    {"tab_id": 7, "name": "beta", "other_focused_clients": ["1", "2"]},
]


@pytest.fixture(autouse=True)
def restore_root_logger():
    """main() strips root handlers and may change the level; put them back."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    for h in handlers:
        root.addHandler(h)
    root.setLevel(level)


@pytest.fixture
def env():
    """Patch config, backend and identity; yield the backend mock."""
    backend = MagicMock()
    backend.list_sessions.return_value = [{"session_name": "main"}]
    backend._list_tabs.return_value = TABS
    with (
        patch("scry.remote.server.read_config_file", return_value=dict(GOOD_CFG)) as cfg,
        patch("scry.remote.server.ZellijBackend", return_value=backend) as cls,
        patch("scry.remote.server.find_session_identity", return_value=IDENT) as ident,
    ):
        backend.cfg, backend.cls, backend.ident = cfg, cls, ident
        yield backend
    backend.ensure_session_group.assert_not_called()


def run(argv):
    out = io.StringIO()
    code = server.main(argv, out=out)
    return code, out.getvalue()


def attach(tab="5", expect="42:1234", proto="1"):
    return run(["remote-attach", f"--tab={tab}", f"--expect-session={expect}", f"--proto={proto}"])


def test_list_ok_payload(env):
    code, text = run(["remote-list", "--proto=1"])
    assert code == 0
    assert unframe(text.encode()) == {
        "proto": 1,
        "status": "ok",
        "session": "main",
        "identity": "42:1234",
        "started_at": IDENT.started_at,
        "tabs": [{"id": 5, "name": "alpha", "clients": 0}, {"id": 7, "name": "beta", "clients": 2}],
    }
    env.cls.assert_called_once_with(wake_keys="\x07\x07", zellij_bin=None)


def test_list_no_session_and_exited_session(env):
    env.list_sessions.return_value = []
    code, text = run(["remote-list", "--proto=1"])
    assert code == 0
    assert unframe(text.encode()) == {"proto": 1, "status": "no_session", "session": "main"}


def test_list_uses_configured_session_group(env):
    env.cfg.return_value = {**GOOD_CFG, "session_group": "other"}
    env.list_sessions.return_value = [{"session_name": "other"}]
    code, text = run(["remote-list", "--proto=1"])
    assert code == 0
    assert unframe(text.encode())["session"] == "other"


def test_list_identity_unavailable(env):
    env.ident.side_effect = IdentityError("no server")
    code, text = run(["remote-list", "--proto=1"])
    assert code == EXIT_IDENTITY
    payload = unframe(text.encode())
    assert payload["status"] == "error" and "no server" in payload["error"]


def test_list_not_zellij(env):
    env.cfg.return_value = {"backend": "tmux", "zellij_wake_keys": "x"}
    code, text = run(["remote-list", "--proto=1"])
    assert code == EXIT_CONFIG
    assert unframe(text.encode())["error"] == "remote backend must be zellij"
    env.cls.assert_not_called()


def test_list_wake_keys_unset(env):
    env.cfg.return_value = {"backend": "zellij"}
    code, text = run(["remote-list", "--proto=1"])
    assert code == EXIT_CONFIG
    assert unframe(text.encode())["error"] == "zellij_wake_keys must be set in the remote ~/.scry.yml"
    env.cls.assert_not_called()


def test_list_bad_backend_construction(env):
    env.cls.side_effect = ValueError("zellij not found in PATH")
    code, text = run(["remote-list", "--proto=1"])
    assert code == EXIT_CONFIG
    assert "zellij not found" in unframe(text.encode())["error"]


def test_list_unexpected_error_is_failure(env):
    env.list_sessions.side_effect = RuntimeError("boom")
    code, text = run(["remote-list", "--proto=1"])
    assert code == server.EXIT_FAILURE
    assert unframe(text.encode())["status"] == "error"


def test_list_proto_mismatch(env):
    code, text = run(["remote-list", "--proto=99"])
    assert code == EXIT_PROTO
    payload = unframe(text.encode())
    assert payload["proto"] == 1 and payload["status"] == "error"
    env.cls.assert_not_called()


def test_attach_stale_identity(env):
    code, text = attach(expect="1:1")
    assert code == EXIT_STALE
    env.attach_window.assert_not_called()
    assert text == ""


def test_attach_tab_gone(env):
    code, _ = attach(tab="99")
    assert code == EXIT_TAB_GONE
    env.attach_window.assert_not_called()


def test_attach_no_session(env):
    env.list_sessions.return_value = []
    code, _ = attach()
    assert code == EXIT_NO_SESSION
    env.attach_window.assert_not_called()


def test_attach_identity_error(env):
    env.ident.side_effect = IdentityError("x")
    code, _ = attach()
    assert code == EXIT_IDENTITY


def test_attach_config_error(env):
    env.cfg.return_value = {}
    code, text = attach()
    assert code == EXIT_CONFIG
    assert text == ""


def test_attach_proto_mismatch(env):
    code, _ = attach(proto="2")
    assert code == EXIT_PROTO


def test_attach_success_calls_attach_window(env):
    code, text = attach()
    assert code == 0
    assert text == ""
    env.attach_window.assert_called_once_with("5", "main")


def test_attach_window_vanishes(env):
    env.attach_window.side_effect = RuntimeError("window 5 no longer exists")
    assert attach()[0] == EXIT_TAB_GONE


def test_attach_window_other_error(env):
    env.attach_window.side_effect = RuntimeError("zellij exploded")
    assert attach()[0] == server.EXIT_FAILURE


def test_attach_sigint_handler_exits_with_failure_code(env):
    original = signal.getsignal(signal.SIGINT)
    try:
        attach()
        handler = signal.getsignal(signal.SIGINT)
        with pytest.raises(SystemExit) as exc:
            handler(signal.SIGINT, None)
        assert exc.value.code == server.EXIT_FAILURE
    finally:
        signal.signal(signal.SIGINT, original)


@pytest.mark.parametrize("exc", [yaml.YAMLError("bad"), PermissionError("denied")])
def test_config_read_error_maps_to_exit_config(env, exc, capsys):
    env.cfg.side_effect = exc
    code, text = run(["remote-list", "--proto=1"])
    assert code == EXIT_CONFIG
    assert unframe(text.encode())["status"] == "error"
    code, text = attach()
    assert code == EXIT_CONFIG and text == ""
    env.cls.assert_not_called()
    assert capsys.readouterr().err == ""


def test_no_stderr_logging(env, capsys):
    logging.getLogger().addHandler(logging.StreamHandler())
    env.list_sessions.side_effect = RuntimeError("boom")
    run(["remote-list", "--proto=1"])
    logging.getLogger("scry.backends.zellij").error("noise")
    attach()
    assert capsys.readouterr().err == ""
    root = logging.getLogger()
    assert not any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in root.handlers
    )


def test_debug_logs_to_file(env, tmp_path):
    log = tmp_path / "scry.log"
    env.cfg.return_value = {**GOOD_CFG, "debug": True, "log_file": str(log)}
    run(["remote-list", "--proto=1"])
    handlers = logging.getLogger().handlers
    assert any(isinstance(h, logging.FileHandler) for h in handlers)
    for h in handlers:
        h.close()


def test_bad_args_exit_2():
    with pytest.raises(SystemExit) as exc:
        server.main(["remote-attach"])
    assert exc.value.code == 2


def test_load_remote_config_reads_file(tmp_path):
    p = tmp_path / "c.yml"
    p.write_text("backend: zellij\nzellij_wake_keys: 'k'\n")
    cfg, err = server.load_remote_config(str(p))
    assert err is None and cfg["session_group"] == "main"
