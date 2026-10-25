"""Tests for scry.remote.config: remotes validation and remote command quoting."""

import logging
import os
import shlex
import subprocess
import sys

import pytest

from scry.remote.config import (
    RemoteConfig,
    default_cache_dir,
    parse_remotes,
    remote_command,
    validate_remote_settings,
)


def _settings(**overrides):
    cfg = {"remotes": None, "remote_cache_dir": None, "remote_timeout": 10, "remote_ctrl_c_guard": True}
    cfg.update(overrides)
    return cfg


def test_parse_none_is_empty():
    assert parse_remotes(None) == []


def test_valid_two_remotes():
    raw = [
        {"key": "a", "label": "Alpha", "host": "alpha.example.com"},
        {"key": "B", "host": "beta", "scry": "~/.local/bin/scry"},
    ]
    assert parse_remotes(raw) == [
        RemoteConfig(key="a", label="Alpha", host="alpha.example.com", scry="scry"),
        RemoteConfig(key="B", label="beta", host="beta", scry="~/.local/bin/scry"),
    ]


def test_must_be_list_of_dicts():
    with pytest.raises(ValueError):
        parse_remotes({"key": "a", "host": "h"})
    with pytest.raises(ValueError, match=r"remotes\[0\]"):
        parse_remotes(["a"])


@pytest.mark.parametrize("key", ["bb", "1", ""])
def test_key_must_be_single_letter(key):
    with pytest.raises(ValueError, match=r"remotes\[0\]"):
        parse_remotes([{"key": key, "host": "h"}])


def test_duplicate_key():
    with pytest.raises(ValueError, match=r"remotes\[1\]: duplicate key 'b'"):
        parse_remotes([{"key": "b", "host": "h1"}, {"key": "b", "host": "h2"}])


def test_host_leading_dash():
    with pytest.raises(ValueError, match="host"):
        parse_remotes([{"key": "a", "host": "-oProxyCommand=x"}])


def test_host_required():
    with pytest.raises(ValueError, match="host"):
        parse_remotes([{"key": "a"}])


def test_bare_command_key_warns(caplog):
    with caplog.at_level(logging.WARNING):
        assert parse_remotes([{"key": "z", "host": "h"}])[0].key == "z"
    assert any(r.levelno == logging.WARNING for r in caplog.records)


@pytest.mark.parametrize("path", ["scry", "/opt/scry", "~/.local/bin/scry"])
def test_scry_path_accepted(path):
    assert parse_remotes([{"key": "a", "host": "h", "scry": path}])[0].scry == path


@pytest.mark.parametrize("path", ["bin/scry", "~user/scry"])
def test_scry_path_policy(path):
    with pytest.raises(ValueError, match="scry"):
        parse_remotes([{"key": "a", "host": "h", "scry": path}])


def test_remote_command_tilde_expands_remotely():
    out = remote_command("~/.local/bin/scry", ["remote-list", "--proto=1"])
    assert out == '"$HOME"/.local/bin/scry remote-list --proto=1'


def test_remote_command_quotes_awkward_values():
    out = remote_command("/opt/my scry", ["--expect-session=1:2 ;rm"])
    assert shlex.split(out) == ["/opt/my scry", "--expect-session=1:2 ;rm"]
    out = remote_command("~/my bin/scry", ["x y"])
    assert shlex.split(out.replace('"$HOME"', "/h")) == ["/h/my bin/scry", "x y"]


def test_default_cache_dir_xdg(monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", "/xdg")
    assert default_cache_dir() == "/xdg/scry/remotes"


def test_default_cache_dir_fallback(monkeypatch):
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    assert default_cache_dir() == os.path.expanduser("~/.cache/scry/remotes")


def test_validate_sets_remotes_and_default_cache_dir(monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", "/xdg")
    cfg = _settings(remotes=[{"key": "a", "host": "h"}])
    validate_remote_settings(cfg)
    assert cfg["remotes"] == [RemoteConfig("a", "h", "h")]
    assert cfg["remote_cache_dir"] == "/xdg/scry/remotes"


def test_validate_expands_cache_dir():
    cfg = _settings(remote_cache_dir="~/x")
    validate_remote_settings(cfg)
    assert cfg["remote_cache_dir"] == os.path.expanduser("~/x")


@pytest.mark.parametrize(
    "key,value",
    [
        ("remote_timeout", 0),
        ("remote_timeout", -1),
        ("remote_timeout", "10"),
        ("remote_timeout", True),
        ("remote_ctrl_c_guard", "yes"),
    ],
)
def test_validate_timeout_and_guard_types(key, value):
    with pytest.raises(ValueError, match=key):
        validate_remote_settings(_settings(**{key: value}))


def test_invalid_remotes_exit_cleanly(tmp_path):
    (tmp_path / ".scry.yml").write_text("remotes:\n  - {key: b, host: h1}\n  - {key: b, host: h2}\n", encoding="utf-8")
    env = {**os.environ, "HOME": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "-c", "import scry.scry"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    assert "duplicate key" in result.stderr
    assert "Traceback" not in result.stderr
