"""Local JSONL pagination is the offline fallback for history."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from server import history


class LocalHistoryPaginationTests(unittest.TestCase):
    def test_page_and_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.jsonl"
            rows = [
                {"type": "scan", "id": f"scan-{i}", "created_at": f"2026-10-{i:02d}T03:00:00+08:00"}
                for i in range(1, 6)
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            old = history.LOCAL_HISTORY
            history.LOCAL_HISTORY = path
            try:
                store = history.HistoryStore()
                events, total, page, pages = store._local_page(2, 2)
            finally:
                history.LOCAL_HISTORY = old
            self.assertEqual(total, 5)
            self.assertEqual(pages, 3)
            self.assertEqual(page, 2)
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0]["id"], "scan-3")

    def test_page_out_of_range_is_clamped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.jsonl"
            path.write_text(json.dumps({"type": "scan", "id": "s", "created_at": "2026-10-01T03:00:00+08:00"}) + "\n", encoding="utf-8")
            old = history.LOCAL_HISTORY
            history.LOCAL_HISTORY = path
            try:
                store = history.HistoryStore()
                events, total, page, pages = store._local_page(99, 10)
            finally:
                history.LOCAL_HISTORY = old
            self.assertEqual((total, pages, page, len(events)), (1, 1, 1, 1))


if __name__ == "__main__":
    unittest.main()
