# -*- coding: utf-8 -*-
"""Multiplexer backend implementations and the factory that selects one."""

from typing import Dict

from scry.backends.base import MuxBackend


def get_backend(cfg: Dict) -> MuxBackend:
    """Instantiate the multiplexer backend named by cfg["backend"].

    Args:
        cfg: The scry config dict; the "backend" key selects the backend
            (missing key defaults to tmux); "zellij_wake_keys" and
            "zellij_bin" are passed to the zellij backend.

    Returns:
        MuxBackend: The instantiated backend.

    Raises:
        ValueError: Unknown backend name, or the backend's binary is not in
            PATH (raised by find_bin_in_path in the backend constructor).
    """
    backend_name = cfg.get("backend", "tmux")

    if backend_name == "tmux":
        from scry.backends.tmux import TmuxBackend

        return TmuxBackend()
    if backend_name == "zellij":
        from scry.backends.zellij import DEFAULT_WAKE_KEYS, ZellijBackend

        return ZellijBackend(
            wake_keys=cfg.get("zellij_wake_keys", DEFAULT_WAKE_KEYS),
            zellij_bin=cfg.get("zellij_bin"),
        )

    raise ValueError(f"unknown backend {backend_name!r}: expected 'tmux' or 'zellij'")
