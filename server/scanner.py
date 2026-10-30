"""Read-only scanner. It never deletes anything."""
from __future__ import annotations

import concurrent.futures
import os
import platform
import socket
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .catalog import FIXED_PATHS, build_categories, build_items
from .config import HOME, LIBRARY, VERSION
from .util import (
    boot_time_iso,
    disk_usage,
    du_many,
    find_local_snapshots,
    human_bytes,
    now_iso,
    run,
    stat_snapshot,
)

ROOTS = {
    "containers": LIBRARY / "Containers",
    "appsupport": LIBRARY / "Application Support",
    "caches": LIBRARY / "Caches",
}

# Extra fixed roots that make the estimate easier to explain. They are measured
# as a group; only the ones declared in catalog.FIXED_PATHS become items.
EXTRA_FIXED = [
    Path("/Library/Logs"),
    Path("/Library/Application Support"),
    Path("/System/Volumes/VM"),
    Path("/private/var/folders"),
    Path("/System/Volumes/Data/.Spotlight-V100"),
]


def _children(root: Path) -> List[Path]:
    try:
        entries = list(os.scandir(str(root)))
    except OSError:
        return []
    result = []
    for entry in entries:
        if entry.name == ".DS_Store":
            continue
        # Do not follow symlinked children during size accounting.
        result.append(Path(entry.path))
    return result


def _scan_children(root: Path) -> Tuple[int, List[dict]]:
    children = _children(root)
    sizes = du_many(children, timeout=240)
    rows = []
    total = 0
    for path in children:
        size = sizes.get(str(path), 0)
        total += size
        rows.append({"path": path, "name": path.name, "size": size})
    rows.sort(key=lambda x: x["size"], reverse=True)
    return total, rows


def _host_info() -> dict:
    model = ""
    code, out, _ = run(["sysctl", "-n", "hw.model"], timeout=5)
    if out:
        model = out.strip()
    return {
        "hostname": socket.gethostname().replace(".local", ""),
        "macos": platform.mac_ver()[0] or "macOS",
        "arch": platform.machine(),
        "model": model,
        "python": platform.python_version(),
        "app_version": VERSION,
    }


def _safe_total(items: List[dict]) -> int:
    return sum(i["bytes"] for i in items if i["cleanable"])


def _review_total(items: List[dict]) -> int:
    return sum(i["bytes"] for i in items if not i["cleanable"] and i["risk"] == "review")


def _library_total(fixed: Dict[str, int], measured: Dict[str, int]) -> int:
    keys = [
        "containers", "appsupport", "caches",
        "group-containers", "library-logs",
        "cloud-storage", "mail", "messages", "metadata", "biome",
        "pnpm-store",
    ]
    path_by_key = {spec["key"]: str(spec["path"]) for spec in FIXED_PATHS}
    total = sum(measured.get(k, 0) for k in ("containers", "appsupport", "caches"))
    for key in keys:
        if key in ("containers", "appsupport", "caches"):
            continue
        total += fixed.get(path_by_key.get(key, ""), 0)
    return total


def _insights(items: List[dict], fixed: Dict[str, int], snapshots: List[str]) -> List[dict]:
    by_label = {i["label"]: i for i in items}
    insights = []
    qq = by_label.get("QQ 容器")
    wechat = by_label.get("微信 容器")
    chat_bytes = (qq["bytes"] if qq else 0) + (wechat["bytes"] if wechat else 0)
    if chat_bytes > 0:
        insights.append({
            "severity": "high",
            "title": "微信 / QQ 是最大的“系统数据”来源",
            "body": f"两者合计 {human_bytes(chat_bytes)}。它们位于 ~/Library/Containers，Finder 不把它们算作普通文件，所以全部落进“系统数据”。这些是聊天记录与文件，不是 macOS 缓存。",
        })

    yarn = by_label.get("Yarn 缓存")
    if yarn and yarn["bytes"] > 1024 ** 3:
        insights.append({
            "severity": "good",
            "title": "有一块可以立即安全释放的缓存",
            "body": f"Yarn 缓存占 {human_bytes(yarn['bytes'])}，由包管理器生成，删除后只会在下次安装时重新下载。",
        })

    docker = by_label.get("Docker 虚拟磁盘")
    if docker and docker["bytes"] > 1024 ** 3:
        insights.append({
            "severity": "medium",
            "title": "Docker 的磁盘镜像被算进了容器数据",
            "body": f"Docker.raw 实际占用 {human_bytes(docker['bytes'])}，逻辑上限约 1 TB。用 Docker Desktop 清理或 docker system prune 回收，别直接删文件。",
        })

    vm = by_label.get("虚拟内存交换区")
    if vm and vm["bytes"] > 5 * 1024 ** 3:
        insights.append({
            "severity": "medium",
            "title": "交换文件不是垃圾",
            "body": f"/System/Volumes/VM 当前 {human_bytes(vm['bytes'])}。它由 macOS 管理，重启后会按需收缩；手动删除会破坏正在运行的系统。",
        })

    if snapshots:
        insights.append({
            "severity": "medium",
            "title": "发现本地 Time Machine 快照",
            "body": f"共 {len(snapshots)} 个快照，可能被计入可清除空间。确认备份已完成后再用 tmutil 删除旧快照。",
        })
    else:
        insights.append({
            "severity": "low",
            "title": "没有本地快照在偷偷占空间",
            "body": "本次检查未发现 Time Machine 本地快照。空间主要来自真实的文件，而不是快照。",
        })

    cleanable = _safe_total(items)
    if cleanable:
        insights.append({
            "severity": "good",
            "title": f"{human_bytes(cleanable)} 可以安全清理",
            "body": "只包含工具白名单里的可重建缓存。编译器缓存、更新包缓存和诊断报告不会影响账号、聊天记录或项目源码。",
        })
    return insights


def _recommendations(items: List[dict]) -> List[dict]:
    recs = [
        {
            "title": "先清缓存，再动聊天记录",
            "body": "安全缓存可以一键回收；聊天记录必须先确认是否需要保留。",
        },
        {
            "title": "微信：设置 → 存储空间",
            "body": "按聊天、图片、视频、文件分类清理，避免误删重要记录。",
        },
        {
            "title": "QQ：设置 → 存储管理",
            "body": "清理聊天文件与缓存；nt_db 中是消息数据库，不要直接删除。",
        },
        {
            "title": "Docker：docker system prune",
            "body": "先运行 docker system df 查看，再清理未使用的镜像和构建缓存。不要删除 Docker.raw。",
        },
    ]
    return recs


def run_scan(progress: Optional[Callable[[int, str], None]] = None) -> dict:
    def report_progress(pct: int, stage: str) -> None:
        if progress:
            try:
                progress(int(pct), stage)
            except Exception:
                pass

    started = time.time()
    report_progress(3, "读取磁盘与系统信息")
    host = _host_info()
    disk = disk_usage(Path("/System/Volumes/Data"))
    snapshots = find_local_snapshots()
    boot = boot_time_iso()

    paths = []
    seen = set()
    for spec in FIXED_PATHS:
        p = spec["path"]
        if str(p) not in seen:
            paths.append(p)
            seen.add(str(p))
    for p in EXTRA_FIXED:
        if str(p) not in seen:
            paths.append(p)
            seen.add(str(p))

    report_progress(10, "正在测量应用容器、缓存与应用数据（只读）")
    measured: Dict[str, int] = {}
    child_rows: Dict[str, List[dict]] = {}
    fixed_sizes: Dict[str, int] = {}
    errors: List[str] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(_scan_children, ROOTS["containers"]): "containers",
            pool.submit(_scan_children, ROOTS["appsupport"]): "appsupport",
            pool.submit(_scan_children, ROOTS["caches"]): "caches",
            pool.submit(du_many, paths, 240): "fixed",
        }
        done = 0
        for future in concurrent.futures.as_completed(futures):
            key = futures[future]
            done += 1
            try:
                result = future.result()
            except Exception as exc:  # a single failed subtree must not fail scan
                errors.append(f"{key}: {exc}")
                result = (0, []) if key != "fixed" else {}
            if key == "fixed":
                fixed_sizes = result
            else:
                total, rows = result
                measured[key] = total
                child_rows[key] = rows
            pct = 15 + int(done / 4 * 55)
            labels = {
                "containers": "应用容器",
                "appsupport": "应用数据",
                "caches": "缓存目录",
                "fixed": "系统目录",
            }
            report_progress(pct, f"已完成 {labels.get(key, key)}，继续测量…")

    for root in ("containers", "appsupport", "caches"):
        if root not in measured:
            measured[root] = 0
            child_rows[root] = []

    report_progress(74, "建立文件清单与安全分级")
    items = build_items({
        "containers": child_rows.get("containers", []),
        "appsupport": child_rows.get("appsupport", []),
        "caches": child_rows.get("caches", []),
        "fixed": fixed_sizes,
    })
    categories = build_categories(
        {
            "containers": child_rows.get("containers", []),
            "appsupport": child_rows.get("appsupport", []),
            "caches": child_rows.get("caches", []),
            "fixed": fixed_sizes,
        },
        items,
    )
    library_bytes = _library_total(fixed_sizes, measured)
    estimate = sum(c["bytes"] for c in categories if c["key"] != "user")
    cleanable = _safe_total(items)
    review = _review_total(items)
    top = sorted(items, key=lambda x: x["bytes"], reverse=True)[:10]
    top_consumers = [
        {
            "id": i["id"],
            "label": i["label"],
            "bytes": i["bytes"],
            "size_label": i["size_label"],
            "category_label": i["category_label"],
            "risk": i["risk"],
            "color": next((c["color"] for c in categories if c["key"] == i["category"]), "#64748b"),
        }
        for i in top
    ]

    report_progress(90, "生成解释与建议")
    finished = time.time()
    report = {
        "id": f"scan-{int(finished)}-{os.getpid()}",
        "created_at": now_iso(),
        "started_at": str(started),
        "duration_ms": int((finished - started) * 1000),
        "version": VERSION,
        "host": host,
        "disk": disk,
        "boot_time": boot,
        "snapshots": snapshots,
        "summary": {
            "system_data_estimate": estimate,
            "library_bytes": library_bytes,
            "cleanable_bytes": cleanable,
            "review_bytes": review,
            "item_count": len(items),
            "cache_bytes": measured.get("caches", 0),
            "container_bytes": measured.get("containers", 0),
            "appsupport_bytes": measured.get("appsupport", 0),
        },
        "categories": categories,
        "items": items,
        "top_consumers": top_consumers,
        "insights": _insights(items, fixed_sizes, snapshots),
        "recommendations": _recommendations(items),
        "errors": errors,
    }
    report_progress(97, "保存报告")
    return report
