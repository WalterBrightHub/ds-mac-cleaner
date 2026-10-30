"""Allow-listed cache deletion with defence in depth.

Nothing here accepts a path from the HTTP client. The client sends item IDs from
a stored scan; the server resolves those IDs to paths and re-checks all safety
conditions immediately before deleting.
"""
from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path
from typing import List, Tuple

from .config import SAFE_DELETE_ROOTS
from .util import is_within, now_iso

_CLEAN_LOCK = threading.Lock()


class CleanupError(Exception):
    pass


def _safe_target(raw_path: str) -> Tuple[Path, Path]:
    path = Path(raw_path)
    # lstat catches symlinks at the entry itself; os.path.realpath catches
    # symlinked ancestors.
    try:
        st = path.lstat()
    except OSError as exc:
        raise CleanupError(f"路径不存在或不可访问：{path} ({exc.strerror})")
    import stat as statmod
    if statmod.S_ISLNK(st.st_mode):
        raise CleanupError(f"拒绝删除符号链接：{path}")
    if os.path.ismount(str(path)):
        raise CleanupError(f"拒绝删除挂载点：{path}")
    real = Path(os.path.realpath(str(path)))
    for root in SAFE_DELETE_ROOTS:
        if is_within(real, root):
            return path, root
    raise CleanupError(f"路径不在允许清理的缓存根目录内：{path}")


def _remove_tree(path: Path) -> None:
    failures = []
    if path.is_dir() and not path.is_symlink():
        def onerror(func, target, exc_info):
            # Retry read-only files once, but never escalate privileges.
            try:
                os.chmod(target, 0o700)
                func(target)
            except Exception as exc:
                failures.append(f"{target}: {exc}")

        shutil.rmtree(str(path), onerror=onerror)
        if failures:
            raise OSError(f"部分文件未能删除（{len(failures)} 个）: {failures[0]}")
        if path.exists():
            raise OSError("目录仍然存在，可能包含受保护文件")
    else:
        path.unlink()
        if path.exists():
            raise OSError("文件仍然存在")


def run_cleanup(report: dict, item_ids: List[str], dry_run: bool = False) -> dict:
    """Delete selected safe items from a stored scan report.

    Returns a result object suitable for the API and history.
    """
    if not isinstance(item_ids, list) or not item_ids:
        raise CleanupError("没有选择任何项目")
    if len(item_ids) > 100:
        raise CleanupError("一次最多清理 100 项")

    by_id = {item["id"]: item for item in report.get("items", [])}
    selected = []
    for iid in dict.fromkeys(item_ids):
        item = by_id.get(iid)
        if item is None:
            raise CleanupError(f"项目不在当前扫描报告中：{iid}")
        if not item.get("cleanable") or item.get("risk") != "safe":
            raise CleanupError(f"项目不允许清理：{item.get('label', iid)}")
        selected.append(item)
    if not selected:
        raise CleanupError("没有可清理的项目")

    if not _CLEAN_LOCK.acquire(blocking=False):
        raise CleanupError("已有一个清理任务正在运行，请稍后")
    started = time.time()
    try:
        results = []
        errors = []
        freed = 0
        requested = sum(int(i.get("bytes", 0)) for i in selected)
        for item in selected:
            path_str = item["path"]
            try:
                path, root = _safe_target(path_str)
            except CleanupError as exc:
                errors.append({"id": item["id"], "label": item.get("label"), "path": path_str, "error": str(exc)})
                continue
            if dry_run:
                results.append({
                    "id": item["id"],
                    "label": item.get("label"),
                    "path": path_str,
                    "bytes": int(item.get("bytes", 0)),
                    "status": "would_delete",
                })
                continue
            before = int(item.get("bytes", 0))
            try:
                _remove_tree(path)
                results.append({
                    "id": item["id"],
                    "label": item.get("label"),
                    "path": path_str,
                    "bytes": before,
                    "status": "deleted",
                })
                freed += before
            except Exception as exc:
                errors.append({
                    "id": item["id"],
                    "label": item.get("label"),
                    "path": path_str,
                    "error": f"{exc.__class__.__name__}: {exc}",
                })

        result = {
            "id": f"clean-{int(time.time())}-{os.getpid()}",
            "created_at": now_iso(),
            "scan_id": report.get("id"),
            "dry_run": bool(dry_run),
            "requested_bytes": requested,
            "freed_bytes": freed,
            "items": results,
            "errors": errors,
            "duration_ms": int((time.time() - started) * 1000),
        }
        return result
    finally:
        _CLEAN_LOCK.release()
