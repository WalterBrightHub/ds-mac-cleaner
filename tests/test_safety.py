"""Safety tests for the only code path that can delete files."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server import cleanup
from server.catalog import classify_cache
from server.util import is_within


def fake_report(items):
    return {"id": "scan-test", "items": items}


def item(path: Path, cleanable=True, risk="safe"):
    return {
        "id": "abc123",
        "label": "test cache",
        "path": str(path),
        "bytes": 1234,
        "cleanable": cleanable,
        "risk": risk,
    }


class CleanupSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_roots = cleanup.SAFE_DELETE_ROOTS
        self.old_exact = cleanup.SAFE_EXACT_DELETE_ROOTS
        cleanup.SAFE_DELETE_ROOTS = [self.root]
        cleanup.SAFE_EXACT_DELETE_ROOTS = []

    def tearDown(self):
        cleanup.SAFE_DELETE_ROOTS = self.old_roots
        cleanup.SAFE_EXACT_DELETE_ROOTS = self.old_exact
        self.tmp.cleanup()

    def test_deletes_directory_inside_allowlist(self):
        target = self.root / "Yarn"
        target.mkdir()
        (target / "file.bin").write_bytes(b"x" * 10)
        result = cleanup.run_cleanup(fake_report([item(target)]), ["abc123"])
        self.assertFalse(target.exists())
        self.assertEqual(result["freed_bytes"], 1234)
        self.assertFalse(result["errors"])

    def test_refuses_outside_allowlist(self):
        outside = self.root.parent / "not-allowed-dsmc"
        outside.mkdir(exist_ok=True)
        try:
            result = cleanup.run_cleanup(fake_report([item(outside)]), ["abc123"])
            self.assertTrue(outside.exists())
            self.assertTrue(result["errors"])
            self.assertIn("不在允许", result["errors"][0]["error"])
        finally:
            outside.rmdir()

    def test_refuses_symlink(self):
        target = self.root / "link"
        real = self.root / "real"
        real.mkdir()
        target.symlink_to(real)
        result = cleanup.run_cleanup(fake_report([item(target)]), ["abc123"])
        self.assertTrue(target.is_symlink())
        self.assertTrue(result["errors"])
        self.assertIn("符号链接", result["errors"][0]["error"])

    def test_refuses_review_item(self):
        target = self.root / "chat"
        target.mkdir()
        with self.assertRaises(cleanup.CleanupError):
            cleanup.run_cleanup(fake_report([item(target, cleanable=False, risk="review")]), ["abc123"])
        self.assertTrue(target.exists())

    def test_dry_run_does_not_delete(self):
        target = self.root / "cache"
        target.mkdir()
        result = cleanup.run_cleanup(fake_report([item(target)]), ["abc123"], dry_run=True)
        self.assertTrue(target.exists())
        self.assertEqual(result["items"][0]["status"], "would_delete")

    def test_root_itself_is_rejected_for_broad_root(self):
        result = cleanup.run_cleanup(fake_report([item(self.root)]), ["abc123"])
        self.assertTrue(result["errors"])

    def test_exact_dedicated_root_is_allowed(self):
        exact = self.root / "DedicatedCache"
        exact.mkdir()
        (exact / "x").write_text("x")
        cleanup.SAFE_EXACT_DELETE_ROOTS = [exact]
        result = cleanup.run_cleanup(fake_report([item(exact)]), ["abc123"])
        self.assertFalse(exact.exists())
        self.assertFalse(result["errors"])

    def test_permission_probe_prevents_mass_attempt(self):
        target = self.root / "Yarn"
        target.mkdir()
        with mock.patch("server.cleanup.os.open", side_effect=PermissionError(1, "Operation not permitted")):
            with self.assertRaises(cleanup.CleanupError) as ctx:
                cleanup.run_cleanup(fake_report([item(target)]), ["abc123"])
        self.assertIn("没有删除权限", str(ctx.exception))
        self.assertTrue(target.exists())

    def test_unknown_id_is_rejected(self):
        with self.assertRaises(cleanup.CleanupError):
            cleanup.run_cleanup(fake_report([]), ["nope"])


class CatalogTests(unittest.TestCase):
    def test_known_cache_is_safe(self):
        self.assertEqual(classify_cache("Yarn"), "safe")
        self.assertEqual(classify_cache("com.tencent.workbuddy.mac.BundleMigration"), "safe")
        self.assertEqual(classify_cache("@deepseek-aidsh-desktop-updater"), "safe")

    def test_unknown_cache_is_review(self):
        self.assertEqual(classify_cache("some-app-private-state"), "review")

    def test_within(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = root / "a" / "b"
            child.mkdir(parents=True)
            self.assertTrue(is_within(child, root))
            self.assertFalse(is_within(root, root))
            self.assertFalse(is_within(Path("/etc"), root))


if __name__ == "__main__":
    unittest.main()
