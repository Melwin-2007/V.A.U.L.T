"""Configuration loader and manager for V.A.U.L.T Agent."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "thresholds.yaml"

DEFAULT_CONFIG: Dict[str, Any] = {
    "confidence_threshold": 0.85,
    "auto_approve_enabled": True,
    "default_pct": 40.0,
    "warn_ratio": 0.75,
    "confidence_multipliers": {"fail": 0.60, "warn": 0.85},
    "confidence_weights": {
        "grounding": 0.40,
        "label": 0.25,
        "unit": 0.20,
        "yoy": 0.15,
    },
    "metrics": {
        "revenue": 50.0,
        "net_income": 150.0,
        "total_assets": 30.0,
        "total_liabilities": 40.0,
        "shareholders_equity": 40.0,
        "operating_cash_flow": 60.0,
        "current_assets": 35.0,
        "current_liabilities": 35.0,
        "cash_and_equivalents": 75.0,
        "diluted_eps": 50.0,
    },
}


class ConfigManager:
    """Thread-safe runtime manager for V.A.U.L.T engine configuration."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or CONFIG_PATH
        self._cached: Dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)
        self.reload()

    def reload(self) -> Dict[str, Any]:
        """Reload configuration from disk; fallback to defaults if missing or invalid."""
        if not self.path.exists():
            self._cached = copy.deepcopy(DEFAULT_CONFIG)
            return self._cached

        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return self._cached

            merged = copy.deepcopy(DEFAULT_CONFIG)
            merged.update(raw)
            if isinstance(raw.get("metrics"), dict):
                merged["metrics"] = {
                    str(k).strip().lower(): float(v) for k, v in raw["metrics"].items()
                }
            self._cached = merged
            return self._cached
        except Exception:
            return self._cached

    def get_config(self) -> Dict[str, Any]:
        """Return a copy of the active configuration."""
        return copy.deepcopy(self._cached)

    def update_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update and persist configuration to disk."""
        current = self.get_config()
        current.update(updates)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(yaml.safe_dump(current, sort_keys=False), encoding="utf-8")
        self._cached = current
        return copy.deepcopy(self._cached)


_GLOBAL_CONFIG_MANAGER = ConfigManager()


def get_config() -> Dict[str, Any]:
    return _GLOBAL_CONFIG_MANAGER.get_config()


def reload_config() -> Dict[str, Any]:
    return _GLOBAL_CONFIG_MANAGER.reload()


def update_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    return _GLOBAL_CONFIG_MANAGER.update_config(updates)
