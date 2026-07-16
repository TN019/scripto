"""Platform data locations.

All app state (config, history, logs) lives in the platform data directory —
never in the current working directory, so Scripto behaves the same no matter
where it is launched from.

`SCRIPTO_DATA_DIR` overrides the base directory (used by tests, and handy for
portable setups).
"""

from __future__ import annotations

import os
from pathlib import Path

import platformdirs

APP_NAME = "Scripto"
ENV_DATA_DIR = "SCRIPTO_DATA_DIR"


def data_dir() -> Path:
    override = os.environ.get(ENV_DATA_DIR)
    base = Path(override).expanduser() if override else Path(
        platformdirs.user_data_dir(APP_NAME, appauthor=False)
    )
    base.mkdir(parents=True, exist_ok=True)
    return base


def config_path() -> Path:
    return data_dir() / "config.json"


def history_path() -> Path:
    return data_dir() / "history.json"


def log_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    """Temporary extracted audio lives here — never next to the user's media."""
    path = data_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path
