"""YAML config loading. Secrets come from environment only, never from YAML."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def load_yaml(name: str, config_dir: Path = CONFIG_DIR) -> dict[str, Any]:
    with open(config_dir / name, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{name} must contain a mapping at top level")
    return data


@lru_cache(maxsize=1)
def settings() -> dict[str, Any]:
    return load_yaml("settings.yaml")


@lru_cache(maxsize=1)
def risk_thresholds() -> dict[str, Any]:
    return load_yaml("risk_thresholds.yaml")
