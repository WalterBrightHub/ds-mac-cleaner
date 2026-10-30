"""Tests for scheduling, settings bounds and unattended selection."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from server.automation import (
    Automation,
    latest_scheduled,
    next_scheduled,
    select_auto_candidates,
)
from server.settings import AutomationState, SettingsStore, validate_settings


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tz = datetime.now().astimezone().tzinfo
        self.now = datetime(2026, 10, 30, 9, 0, tzinfo=self.tz)  # Friday

    def test_daily_latest_and_next(self):
        cfg = {"enabled": True, "frequency": "daily", "time": "03:00", "weekdays": [1]}
        latest = latest_scheduled(self.now, cfg)
        nxt = next_scheduled(self.now, cfg)
        self.assertEqual(latest.date().isoformat(), "2026-10-30")
        self.assertEqual(latest.strftime("%H:%M"), "03:00")
        self.assertEqual(nxt.date().isoformat(), "2026-10-31")

    def test_weekly_skips_other_days(self):
        cfg = {"enabled": True, "frequency": "weekly", "time": "04:30", "weekdays": [7]}
        latest = latest_scheduled(self.now, cfg)
        nxt = next_scheduled(self.now, cfg)
        self.assertEqual(latest.isoweekday(), 7)
        self.assertEqual(latest.date().isoformat(), "2026-10-25")
        self.assertEqual(nxt.date().isoformat(), "2026-11-01")

    def test_disabled(self):
        cfg = {"enabled": False, "frequency": "daily", "time": "03:00", "weekdays": [1]}
        self.assertIsNone(latest_scheduled(self.now, cfg))
        self.assertIsNone(next_scheduled(self.now, cfg))


class AutoSelectionTests(unittest.TestCase):
    def item(self, name, size, auto=True, cleanable=True):
        return {"id": name, "label": name, "bytes": size, "auto_clean": auto, "cleanable": cleanable}

    def test_only_auto_clean_items_and_cap(self):
        gib = 1024 ** 3
        report = {"items": [
            self.item("yarn", 10 * gib),
            self.item("updater", 4 * gib),
            self.item("manual-cache", 9 * gib, auto=False),
            self.item("chat", 40 * gib, auto=False, cleanable=False),
            self.item("too-big", 20 * gib),
        ]}
        selected, total = select_auto_candidates(report, 15)
        ids = {item["id"] for item in selected}
        self.assertEqual(ids, {"yarn", "updater"})
        self.assertEqual(total, 14 * gib)

    def test_empty_when_disabled(self):
        report = {"items": [self.item("yarn", 10, auto=False)]}
        selected, total = select_auto_candidates(report, 20)
        self.assertEqual(selected, [])
        self.assertEqual(total, 0)


class SettingsTests(unittest.TestCase):
    def test_defaults_are_safe(self):
        settings = validate_settings({})
        self.assertTrue(settings["scan"]["enabled"])
        self.assertFalse(settings["auto_clean"]["enabled"])
        self.assertEqual(settings["auto_clean"]["only_below_free_gb"], 25)

    def test_invalid_time_rejected(self):
        with self.assertRaises(ValueError):
            validate_settings({"scan": {"time": "25:99"}})

    def test_bounds_clamped(self):
        settings = validate_settings({"auto_clean": {"max_total_gb": 9999}, "history_retention_days": 1})
        self.assertEqual(settings["auto_clean"]["max_total_gb"], 500)
        self.assertEqual(settings["history_retention_days"], 7)

    def test_store_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SettingsStore(Path(tmp) / "settings.json")
            updated = store.update({"scan": {"time": "05:15", "frequency": "weekly"}})
            self.assertEqual(updated["scan"]["time"], "05:15")
            again = SettingsStore(Path(tmp) / "settings.json").get()
            self.assertEqual(again["scan"]["frequency"], "weekly")


class AutoCleanFlowTests(unittest.TestCase):
    def test_runs_only_auto_items_when_below_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SettingsStore(Path(tmp) / "settings.json")
            store.update({
                "auto_clean": {
                    "enabled": True, "only_below_free_gb": 100,
                    "max_total_gb": 1, "min_interval_hours": 24,
                },
            })
            state = AutomationState(Path(tmp) / "state.json")
            report = {
                "id": "scan-auto-test",
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "disk": {"free": 1 * 1024 ** 3},
                "items": [
                    {"id": "a", "label": "auto", "bytes": 500 * 1024 ** 2,
                     "cleanable": True, "auto_clean": True},
                    {"id": "b", "label": "manual", "bytes": 800 * 1024 ** 2,
                     "cleanable": True, "auto_clean": False},
                    {"id": "c", "label": "chat", "bytes": 999 * 1024 ** 2,
                     "cleanable": False, "auto_clean": False},
                ],
            }
            tasks = mock.Mock()
            tasks.active_scan.return_value = None
            history = mock.Mock()
            captured = {}

            def fake_cleanup(report_arg, ids, dry_run=False):
                captured["ids"] = ids
                return {
                    "id": "clean-auto-test",
                    "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "scan_id": report_arg["id"],
                    "dry_run": False,
                    "freed_bytes": 500 * 1024 ** 2,
                    "items": [{"id": "a", "bytes": 500 * 1024 ** 2}],
                    "errors": [],
                }

            automation = Automation(store, state, tasks, history, lambda: report)
            with mock.patch("server.automation.run_cleanup", side_effect=fake_cleanup):
                automation._maybe_auto_clean(store.get(), datetime.now().astimezone())

            self.assertEqual(captured["ids"], ["a"])
            history.record_cleanup.assert_called_once()
            self.assertIsNotNone(state.get().get("last_auto_clean_at"))

    def test_skips_when_above_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SettingsStore(Path(tmp) / "settings.json")
            store.update({"auto_clean": {"enabled": True, "only_below_free_gb": 10}})
            state = AutomationState(Path(tmp) / "state.json")
            report = {
                "id": "scan", "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "disk": {"free": 100 * 1024 ** 3}, "items": [],
            }
            tasks = mock.Mock()
            tasks.active_scan.return_value = None
            history = mock.Mock()
            automation = Automation(store, state, tasks, history, lambda: report)
            with mock.patch("server.automation.run_cleanup") as cleanup:
                automation._maybe_auto_clean(store.get(), datetime.now().astimezone())
                cleanup.assert_not_called()
            history.record_cleanup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
