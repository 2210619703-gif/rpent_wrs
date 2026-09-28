"""Load API settings from local config.py, then the environment."""

from __future__ import annotations

import os
from pathlib import Path


def apply_local_config() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "config.py"
    if not path.is_file():
        return
    ns: dict = {}
    exec(path.read_text(encoding="utf-8"), ns)
    for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "RPENT_WRS_MODEL"):
        value = str(ns.get(name) or "").strip()
        if value and not os.environ.get(name):
            os.environ[name] = value
