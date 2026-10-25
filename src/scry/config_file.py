"""Shared reader for the scry config file.

Kept free of imports from scry.scry (which parses argv and builds a backend at import time) so the headless remote
server can use it.
"""

import os
from typing import Dict

import yaml

CONFIG_PATH = "~/.scry.yml"


def read_config_file(path: str = CONFIG_PATH) -> Dict:
    """Read the YAML config file.

    Args:
        path: Config file path; ``~`` is expanded.

    Returns:
        Dict: The parsed config, or an empty dict if the file is missing or empty.

    Raises:
        yaml.YAMLError: If the file is not valid YAML.
    """
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
