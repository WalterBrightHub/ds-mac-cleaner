"""Persistent automation settings for the always-on cleaner."""
from __future__ import annotations

import copy
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict

from .config import RUNTIME_DIR
from .util import atomic_write_json, read_json

SETTINGS_PATH = RUNTIME_DIR / "settings.json"
STATE_PATH = RUNTIME_DIR / "automation_state.json"

DEFAULT_SETTINGS: Dict[str, Any] = {
    "scan": {
        "enabled": True,
        "frequency": "daily",
        "time": "03:00",
        "weekdays": [1, 2, 3, 4, 5, 6, 7],
    },
    "auto_clean": {
        "enabled": False,
        "only_below_free_gb": 25,
        "max_total_gb": 20,
        "min_interval_hours": 24,
    },
    "history_retention_days": 180,
}

TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _as_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return default


def _int_in(value, low: int, high: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def validate_settings(data: dict) -> dict:
    """Merge user input onto defaults and enforce safe bounds."""
    if not isinstance(data, dict):
        raise ValueError("settings must be a JSON object")
    merged = _merge(DEFAULT_SETTINGS, data)
    scan = merged.get("scan") or {}
    auto = merged.get("auto_clean") or {}

    frequency = scan.get("frequency", "daily")
    if frequency not in ("daily", "weekly"):
        raise ValueError("scan.frequency must be daily or weekly")
    time_value = str(scan.get("time", "03:00")).strip()
    if not TIME_RE.match(time_value):
        raise ValueError("scan.time must be HH:MM")
    weekdays = scan.get("weekdays", [1, 2, 3, 4, 5, 6, 7])
    if not isinstance(weekdays, list):
        weekdays = [1, 2, 3, 4, 5, 6, 7]
    clean_days = sorted({int(day) for day in weekdays if str(day).isdigit() and 1 <= int(day) <= 7})
    if not clean_days:
        clean_days = [1, 2, 3, 4, 5, 6, 7]

    validated = {
        "scan": {
            "enabled": _as_bool(scan.get("enabled"), True),
            "frequency": frequency,
            "time": time_value,
            "weekdays": clean_days,
        },
        "auto_clean": {
            "enabled": _as_bool(auto.get("enabled"), False),
            "only_below_free_gb": _int_in(auto.get("only_below_free_gb"), 0, 2000, 25),
            "max_total_gb": _int_in(auto.get("max_total_gb"), 1, 500, 20),
            "min_interval_hours": _int_in(auto.get("min_interval_hours"), 1, 720, 24),
        },
        "history_retention_days": _int_in(merged.get("history_retention_days"), 7, 3650, 180),
    }
    return validated


class SettingsStore:
    def __init__(self, path: Path = SETTINGS_PATH) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._settings = validate_settings(read_json(path, {}) or {})

    def get(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._settings)

    def update(self, patch: dict) -> dict:
        with self._lock:
            self._settings = validate_settings(_merge(self._settings, patch))
            atomic_write_json(self.path, self._settings)
            return copy.deepcopy(self._settings)

    def reload(self) -> dict:
        with self._lock:
            self._settings = validate_settings(read_json(self.path, {}) or {})
            return copy.deepcopy(self._settings)


class AutomationState:
    def __init__(self, path: Path = STATE_PATH) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._state = read_json(path, {}) or {}

    def get(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._state)

    def mark(self, **kwargs) -> dict:
        with self._lock:
            self._state.update(kwargs)
            atomic_write_json(self.path, self._state)
            return copy.deepcopy(self._state)

    def set_last_result(self, result: dict) -> dict:
        return self.mark(last_result=result)
