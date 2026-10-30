"""Always-on scheduler: periodic read-only scans and conservative auto-clean."""
from __future__ import annotations

import copy
import sys
import threading
import time
from datetime import datetime, timedelta
from typing import Callable, Optional

from .cleanup import CleanupError, run_cleanup
from .history import HistoryStore
from .settings import AutomationState, SettingsStore
from .util import human_bytes, now_iso

GIB = 1024 ** 3


def _parse_iso(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.astimezone()
        return dt
    except (ValueError, TypeError):
        return None


def _at_time(day, hhmm: str, tz):
    hour, minute = [int(part) for part in hhmm.split(":")]
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)


def latest_scheduled(now: datetime, scan_cfg: dict) -> Optional[datetime]:
    """Most recent scheduled time at or before now."""
    if not scan_cfg.get("enabled", True):
        return None
    weekdays = set(scan_cfg.get("weekdays") or [])
    daily = scan_cfg.get("frequency", "daily") == "daily"
    for offset in range(0, 9):
        day = now.date() - timedelta(days=offset)
        if not daily and day.isoweekday() not in weekdays:
            continue
        candidate = _at_time(day, scan_cfg.get("time", "03:00"), now.tzinfo)
        if candidate <= now:
            return candidate
    return None


def select_auto_candidates(report: dict, max_total_gb: int) -> tuple:
    """Only AUTO-marked, cleanable caches, newest/largest first, under a cap."""
    candidates = [
        item for item in (report or {}).get("items", [])
        if item.get("cleanable") and item.get("auto_clean") and int(item.get("bytes", 0)) > 0
    ]
    candidates.sort(key=lambda item: int(item.get("bytes", 0)), reverse=True)
    cap = int(max_total_gb) * GIB
    selected = []
    total = 0
    for item in candidates:
        size = int(item.get("bytes", 0))
        if total + size <= cap:
            selected.append(item)
            total += size
    return selected, total


def next_scheduled(now: datetime, scan_cfg: dict) -> Optional[datetime]:
    if not scan_cfg.get("enabled", True):
        return None
    weekdays = set(scan_cfg.get("weekdays") or [])
    daily = scan_cfg.get("frequency", "daily") == "daily"
    for offset in range(0, 10):
        day = now.date() + timedelta(days=offset)
        if not daily and day.isoweekday() not in weekdays:
            continue
        candidate = _at_time(day, scan_cfg.get("time", "03:00"), now.tzinfo)
        if candidate > now:
            return candidate
    return None


class Automation:
    def __init__(
        self,
        settings: SettingsStore,
        state: AutomationState,
        tasks,
        history: HistoryStore,
        latest_provider: Callable[[], Optional[dict]],
    ) -> None:
        self.settings = settings
        self.state = state
        self.tasks = tasks
        self.history = history
        self.latest_provider = latest_provider
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._last_prune_day = None
        self._last_skip = None

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        cfg = self.settings.get()
        state = self.state.get()
        now = datetime.now().astimezone()
        next_run = next_scheduled(now, cfg["scan"])
        latest = self.latest_provider() or {}
        return {
            "scan": {
                **cfg["scan"],
                "next_at": next_run.isoformat(timespec="seconds") if next_run else None,
                "last_auto_at": state.get("last_auto_scan_at"),
                "last_scan_at": latest.get("created_at"),
            },
            "auto_clean": {
                **cfg["auto_clean"],
                "last_at": state.get("last_auto_clean_at"),
                "last_result": state.get("last_result"),
            },
            "history_retention_days": cfg["history_retention_days"],
            "running": bool(self.tasks.active_scan()),
            "server_time": now_iso(),
        }

    def reload(self) -> dict:
        self.settings.reload()
        return self.status()

    # ------------------------------------------------------------------- loop
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="dsmc-automation")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        # Give the HTTP server a moment to bind before any automatic work.
        self._stop.wait(4)
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                print(f"[automation] {exc.__class__.__name__}: {exc}", file=sys.stderr, flush=True)
            self._stop.wait(15)

    def tick(self) -> None:
        cfg = self.settings.get()
        now = datetime.now().astimezone()

        if cfg["scan"]["enabled"] and not self.tasks.active_scan():
            latest = self.latest_provider() or {}
            latest_ts = _parse_iso(latest.get("created_at"))
            due = latest_scheduled(now, cfg["scan"])
            if due and (latest_ts is None or latest_ts < due):
                task = self.tasks.start_scan(trigger="auto")
                if task.get("trigger") == "auto":
                    self.state.mark(last_auto_scan_at=now_iso(), last_auto_scan_id=task.get("id"))
                    print(f"[automation] auto scan started: {task.get('id')}", flush=True)

        if cfg["auto_clean"]["enabled"]:
            self._maybe_auto_clean(cfg, now)

        if self._last_prune_day != now.date():
            try:
                counts = self.history.prune(cfg["history_retention_days"])
                self._last_prune_day = now.date()
                if counts.get("scans") or counts.get("cleanups"):
                    print(f"[automation] pruned history: {counts}", flush=True)
            except Exception as exc:
                print(f"[automation] prune failed: {exc}", file=sys.stderr, flush=True)

    def _maybe_auto_clean(self, cfg: dict, now: datetime) -> None:
        if not self._lock.acquire(blocking=False):
            return
        try:
            if self.tasks.active_scan():
                return
            latest = self.latest_provider() or {}
            if not latest:
                return
            latest_ts = _parse_iso(latest.get("created_at"))
            if latest_ts is None or (now - latest_ts).total_seconds() > 6 * 3600:
                self._skip("报告超过 6 小时，等待下一次扫描")
                return

            free = int((latest.get("disk") or {}).get("free") or 0)
            threshold = int(cfg["auto_clean"]["only_below_free_gb"]) * GIB
            if threshold and free > threshold:
                self._skip(f"可用空间仍高于 {cfg['auto_clean']['only_below_free_gb']} GB")
                return

            state = self.state.get()
            last_clean = _parse_iso(state.get("last_auto_clean_at"))
            interval = int(cfg["auto_clean"]["min_interval_hours"]) * 3600
            if last_clean and (now - last_clean).total_seconds() < interval:
                self._skip("尚未达到自动清理间隔")
                return

            selected, total = select_auto_candidates(
                latest, int(cfg["auto_clean"]["max_total_gb"]),
            )
            if not selected:
                self._skip("没有符合自动清理条件的缓存，或超过单次上限")
                return

            ids = [item["id"] for item in selected]
            result = run_cleanup(latest, ids, dry_run=False)
            result["trigger"] = "auto"
            result["reason"] = f"可用空间 {human_bytes(free)}，低于阈值 {cfg['auto_clean']['only_below_free_gb']} GB"
            try:
                self.history.record_cleanup(result)
            except Exception:
                pass
            self.state.mark(
                last_auto_clean_at=now_iso(),
                last_result={
                    "at": result["created_at"],
                    "freed_bytes": result.get("freed_bytes", 0),
                    "item_count": len(result.get("items", [])),
                    "error_count": len(result.get("errors", [])),
                    "reason": result.get("reason"),
                },
            )
            self._last_skip = None
            print(
                f"[automation] auto clean freed {human_bytes(result.get('freed_bytes', 0))} "
                f"from {len(selected)} caches",
                flush=True,
            )
        except CleanupError as exc:
            self._skip(f"自动清理暂时不可执行：{exc}")
        finally:
            self._lock.release()

    def _skip(self, reason: str) -> None:
        # Avoid writing state every 15 seconds; only log when the reason changes.
        if reason != self._last_skip:
            self._last_skip = reason
            print(f"[automation] auto clean skipped: {reason}", flush=True)
