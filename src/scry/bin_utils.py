# -*- coding: utf-8 -*-

import logging
import os

_LOGGER = logging.getLogger(__name__)


def find_bin_in_path(binary_name: str) -> str:
    """Find location of binary_name in PATH

    Args:
        binary_name: name of binary to search path for

    Returns:
        str: full pathname of binary

    Raises:
        ValueError: the binary is not found in the path

    """

    for path in os.environ["PATH"].split(os.pathsep):
        bin_full_path = os.path.join(path, binary_name)
        if os.access(bin_full_path, os.X_OK):
            _LOGGER.debug("found tmux: %s", bin_full_path)
            return bin_full_path

    raise ValueError(f"{binary_name} not found in PATH")
