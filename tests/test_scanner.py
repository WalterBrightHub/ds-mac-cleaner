"""Cancellation must be checked before a scan touches the filesystem."""
from __future__ import annotations

import threading
import unittest

from server.scanner import ScanCancelled, run_scan


class ScanCancellationTests(unittest.TestCase):
    def test_cancel_before_scan_starts(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(ScanCancelled):
            run_scan(cancel_event=event)


if __name__ == "__main__":
    unittest.main()
