"""Cross-platform data/config locations.

Windows:  %LOCALAPPDATA%\\tokenfold          (falls back to ~\\AppData\\Local)
Linux:    $XDG_DATA_HOME/tokenfold  (falls back to ~/.local/share/tokenfold)
          $XDG_CONFIG_HOME/tokenfold (falls back to ~/.config/tokenfold)
macOS:    ~/Library/Application Support/tokenfold

Override everything with $TOKENFOLD_HOME.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _base_data_dir() -> Path:
    override = os.environ.get("TOKENFOLD_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(root) / "tokenfold"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "tokenfold"
    root = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(root) / "tokenfold"


def data_dir() -> Path:
    d = _base_data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_dir() -> Path:
    if os.environ.get("TOKENFOLD_HOME"):
        return data_dir()
    if sys.platform in ("win32", "darwin"):
        return data_dir()
    root = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    d = Path(root) / "tokenfold"
    d.mkdir(parents=True, exist_ok=True)
    return d


def dictionaries_dir() -> Path:
    d = data_dir() / "dictionaries"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sessions_dir() -> Path:
    d = data_dir() / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def metrics_db_path() -> Path:
    return data_dir() / "metrics.sqlite3"


def cache_dir() -> Path:
    d = data_dir() / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d
