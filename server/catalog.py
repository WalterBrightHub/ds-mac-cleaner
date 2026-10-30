"""The allow-list catalog that turns measurements into reviewable items.

Design rule: a path is only cleanable when it is an explicitly recognized,
regenerable cache. Everything else is report-only. This keeps the tool useful
without letting a heuristic delete someone's chat history or source code.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .config import (
    APP_SUPPORT_NAMES,
    CONTAINER_NAMES,
    HOME,
    LIBRARY,
)
from .util import display_path, human_bytes, item_id, stat_snapshot

CATEGORY_META = {
    "containers": {"label": "应用容器", "color": "#ff8a5b", "order": 1},
    "appsupport": {"label": "应用数据", "color": "#f4c95d", "order": 2},
    "caches": {"label": "缓存", "color": "#6ee7b7", "order": 3},
    "dev": {"label": "开发工具", "color": "#7dd3fc", "order": 4},
    "media": {"label": "聊天与媒体", "color": "#c4b5fd", "order": 5},
    "system": {"label": "系统与虚拟内存", "color": "#94a3b8", "order": 6},
    "user": {"label": "个人文件", "color": "#f9a8d4", "order": 7},
}

# Caches whose contents are unambiguously regenerable. Unknown caches stay
# report-only: some apps misuse Caches/ for state that is not a cache.
SAFE_CACHE_EXACT = {
    "Yarn",
    "pnpm",
    "npm",
    "pip",
    "Homebrew",
    "node-gyp",
    "ms-playwright",
    "Cypress",
    "typescript",
    "electron",
    "electron-builder",
    "Google",
    "Mozilla",
    "Firefox",
    "aws",
    "Codex",
    "CocoaPods",
    "go-build",
    "Sentry",
    "TRAE SOLO CN",
    "Trae CN",
    "微信开发者工具",
    "Quark",
    "QoderCN",
    "@guanjia-openclawelectron-updater",
    "@deepseek-aidsh-desktop-updater",
    "com.tencent.workbuddy.mac.BundleMigration",
    "xterminal-updater",
    "Adobe",
}
SAFE_CACHE_RE = re.compile(
    r"(?i)(^|[.\-_])(cache|caches|updater|update|playwright|cypress|node-gyp|"
    r"electron|typescript|yarn|pnpm|npm|pip|homebrew)([.\-_]|$)|"
    r"(cache|updater)$"
)

CACHE_NOTES = {
    "Yarn": "Yarn 全局包缓存。删除后下次安装依赖会重新下载，不影响项目源码。",
    "pnpm": "pnpm 的包缓存。删除后下次安装会重新下载；当前项目里的 node_modules 不受影响。",
    "npm": "npm 包缓存。可安全重建。",
    "pip": "Python 安装包缓存。可安全重建。",
    "Homebrew": "Homebrew 下载缓存。不会卸载已安装的软件。",
    "node-gyp": "node-gyp 编译缓存。下次编译会重建。",
    "ms-playwright": "Playwright 浏览器二进制。删除后需重新执行 playwright install。",
    "Cypress": "Cypress 二进制缓存。删除后需重新安装。",
    "typescript": "TypeScript 编译缓存。可安全重建。",
    "Google": "Chrome 组件、更新与 GPU 缓存；不包含书签、密码和浏览历史。",
    "Mozilla": "Firefox 缓存；不包含书签和密码。",
    "Codex": "Codex 更新与运行缓存。",
    "TRAE SOLO CN": "TRAE 更新与运行缓存。",
    "Trae CN": "TRAE 更新与运行缓存。",
    "微信开发者工具": "微信开发者工具缓存；不包含项目源码。",
    "com.tencent.workbuddy.mac.BundleMigration": "WorkBuddy 升级迁移缓存。",
    "@guanjia-openclawelectron-updater": "Electron 应用更新包缓存。",
    "@deepseek-aidsh-desktop-updater": "DeepSeek 桌面端更新包缓存。",
}

KNOWN_CONTAINER_NOTES = {
    "com.tencent.qq": "QQ 的消息数据库和聊天文件。删除会丢聊天记录，必须在 QQ 内按会话/文件类型清理。",
    "com.tencent.xinWeChat": "微信的聊天图片、视频、文件和数据库。删除会丢聊天记录，请使用微信自带的存储空间管理。",
    "5ZSL2CJU2T.com.dingtalk.mac": "钉钉本地数据与缓存。建议在钉钉内清理。",
    "com.tencent.WeWorkMac": "企业微信本地数据。建议在企业微信内清理。",
}

REVIEW_HINT = "报告只读：需要你确认后到对应 App 或 Finder 中处理。"

# Fixed, non-container paths. kind is used by the UI for icon + explanation.
FIXED_PATHS = [
    {
        "key": "diagnostic",
        "label": "崩溃与诊断报告",
        "path": LIBRARY / "Logs" / "DiagnosticReports",
        "category": "system",
        "kind": "logs",
        "risk": "safe",
        "cleanable": True,
        "description": "应用崩溃日志，删除后系统会按需重新生成。",
        "hint": "可安全清理。",
    },
    {
        "key": "xcode-derived",
        "label": "Xcode DerivedData",
        "path": LIBRARY / "Developer" / "Xcode" / "DerivedData",
        "category": "dev",
        "kind": "code",
        "risk": "safe",
        "cleanable": True,
        "description": "Xcode 的索引与编译中间产物，删除后首次编译会变慢。",
        "hint": "可安全清理，建议关闭 Xcode 后执行。",
    },
    {
        "key": "sim-cache",
        "label": "iOS 模拟器缓存",
        "path": LIBRARY / "Developer" / "CoreSimulator" / "Caches",
        "category": "dev",
        "kind": "code",
        "risk": "safe",
        "cleanable": True,
        "description": "模拟器运行缓存，可重建。不会删除已安装的模拟器。",
        "hint": "可安全清理。",
    },
    {
        "key": "npm-cache",
        "label": "npm 缓存（~/.npm）",
        "path": HOME / ".npm" / "_cacache",
        "category": "dev",
        "kind": "cache",
        "risk": "safe",
        "cleanable": True,
        "description": "npm 的内容寻址缓存，可安全重建。",
        "hint": "可安全清理。",
    },
    {
        "key": "pnpm-store",
        "label": "pnpm 内容仓库",
        "path": LIBRARY / "pnpm",
        "category": "dev",
        "kind": "code",
        "risk": "review",
        "cleanable": False,
        "description": "pnpm 的全局内容仓库，已有项目可能通过硬链接引用它。",
        "hint": "建议运行 pnpm store prune，而不是直接删除。",
    },
    {
        "key": "group-containers",
        "label": "App Group 数据",
        "path": LIBRARY / "Group Containers",
        "category": "appsupport",
        "kind": "app",
        "risk": "review",
        "cleanable": False,
        "description": "应用之间共享的数据，可能包含登录状态和文档。",
        "hint": "报告只读，请勿整体删除。",
    },
    {
        "key": "library-logs",
        "label": "用户日志",
        "path": LIBRARY / "Logs",
        "category": "system",
        "kind": "logs",
        "risk": "review",
        "cleanable": False,
        "description": "应用日志目录。诊断报告可单独安全清理，其他日志请按应用判断。",
        "hint": "只允许清理其中的 DiagnosticReports。",
    },
    {
        "key": "mail",
        "label": "邮件本地数据",
        "path": LIBRARY / "Mail",
        "category": "media",
        "kind": "media",
        "risk": "review",
        "cleanable": False,
        "description": "Mail 的本地邮件、附件与索引。可在 Mail 内管理下载项。",
        "hint": "报告只读，避免丢失离线邮件。",
    },
    {
        "key": "messages",
        "label": "iMessage 数据",
        "path": LIBRARY / "Messages",
        "category": "media",
        "kind": "media",
        "risk": "review",
        "cleanable": False,
        "description": "信息 App 的附件与数据库。",
        "hint": "报告只读。",
    },
    {
        "key": "cloud-storage",
        "label": "云盘本地缓存",
        "path": LIBRARY / "CloudStorage",
        "category": "appsupport",
        "kind": "cloud",
        "risk": "review",
        "cleanable": False,
        "description": "OneDrive、iCloud Drive 等云盘的本地副本，可能包含尚未上传的文件。",
        "hint": "请通过对应云盘客户端管理离线文件。",
    },
    {
        "key": "metadata",
        "label": "Spotlight 元数据",
        "path": LIBRARY / "Metadata",
        "category": "system",
        "kind": "system",
        "risk": "review",
        "cleanable": False,
        "description": "Spotlight 索引。删除会导致重新建立索引，不建议手动处理。",
        "hint": "报告只读。",
    },
    {
        "key": "biome",
        "label": "系统行为数据库",
        "path": LIBRARY / "Biome",
        "category": "system",
        "kind": "system",
        "risk": "review",
        "cleanable": False,
        "description": "macOS 的本地行为与建议数据。",
        "hint": "报告只读。",
    },
    {
        "key": "downloads",
        "label": "下载文件夹",
        "path": HOME / "Downloads",
        "category": "user",
        "kind": "download",
        "risk": "review",
        "cleanable": False,
        "description": "你的个人文件，不属于系统垃圾。",
        "hint": "请在 Finder 中自行整理。",
    },
    {
        "key": "trash",
        "label": "废纸篓",
        "path": HOME / ".Trash",
        "category": "user",
        "kind": "trash",
        "risk": "review",
        "cleanable": False,
        "description": "废纸篓中的文件。清空后才会释放空间。",
        "hint": "请在 Finder 中确认后清空。",
    },
    {
        "key": "mobile-backup",
        "label": "iPhone / iPad 备份",
        "path": LIBRARY / "Application Support" / "MobileSync" / "Backup",
        "category": "media",
        "kind": "backup",
        "risk": "review",
        "cleanable": False,
        "description": "Finder / iMazing 等创建的 iOS 本地备份。",
        "hint": "请在 Finder 的设备管理里删除旧备份。",
    },
    {
        "key": "vm-swap",
        "label": "虚拟内存交换区",
        "path": Path("/System/Volumes/VM"),
        "category": "system",
        "kind": "memory",
        "risk": "review",
        "cleanable": False,
        "description": "macOS 的 swap 文件。系统按需创建，重启后会收缩。",
        "hint": "不要手动删除。重启 Mac 是最安全的回收方式。",
    },
    {
        "key": "var-folders",
        "label": "系统临时文件",
        "path": Path("/private/var/folders"),
        "category": "system",
        "kind": "system",
        "risk": "review",
        "cleanable": False,
        "description": "系统与应用的临时目录，重启后会自动清理一部分。",
        "hint": "报告只读，避免破坏正在运行的应用。",
    },
    {
        "key": "library-appsupport",
        "label": "/Library 应用支持",
        "path": Path("/Library/Application Support"),
        "category": "system",
        "kind": "system",
        "risk": "review",
        "cleanable": False,
        "description": "系统级应用数据，可能包含驱动、Adobe、输入法等。",
        "hint": "报告只读。",
    },
    {
        "key": "library-caches",
        "label": "/Library 缓存",
        "path": Path("/Library/Caches"),
        "category": "system",
        "kind": "cache",
        "risk": "review",
        "cleanable": False,
        "description": "系统级共享缓存，通常很小且需要管理员权限。",
        "hint": "报告只读。",
    },
]


def classify_cache(name: str) -> str:
    if name in SAFE_CACHE_EXACT:
        return "safe"
    if SAFE_CACHE_RE.search(name):
        return "safe"
    return "review"


def _friendly_container(name: str) -> str:
    return CONTAINER_NAMES.get(name, name)


def _friendly_app_support(name: str) -> str:
    return APP_SUPPORT_NAMES.get(name, name)


def _base_item(path: Path, category: str, kind: str, risk: str, cleanable: bool,
               label: str, description: str, hint: str, size: int) -> dict:
    snap = stat_snapshot(path)
    return {
        "id": item_id(path, kind),
        "label": label,
        "path": str(path),
        "display_path": display_path(path, HOME),
        "category": category,
        "category_label": CATEGORY_META.get(category, {}).get("label", category),
        "kind": kind,
        "bytes": int(size),
        "size_label": human_bytes(size),
        "risk": risk,
        "cleanable": bool(cleanable and risk == "safe"),
        "selected_default": bool(cleanable and risk == "safe"),
        "description": description,
        "hint": hint,
        "modified": snap["mtime"],
        "logical_size": snap["logical_size"],
        "exists": path.exists() or path.is_symlink(),
    }


def build_items(measurements: dict) -> List[dict]:
    """Build the displayed catalog from scanner measurements.

    measurements keys: containers, appsupport, caches, fixed.
    Each child measurement is a dict: path, size, name.
    """
    items: List[dict] = []

    # App sandbox containers. Docker is handled separately to avoid showing the
    # sparse 1 TB Docker.raw as a 1 TB container.
    for child in measurements.get("containers", []):
        p: Path = child["path"]
        name = child["name"]
        if name == "com.docker.docker":
            continue
        if child["size"] < 10 * 1024 * 1024:
            continue
        label = f"{_friendly_container(name)} 容器"
        desc = KNOWN_CONTAINER_NOTES.get(
            name,
            "应用沙盒中的本地数据、缓存和文档。整体删除可能导致应用重置或数据丢失。",
        )
        items.append(_base_item(
            p, "containers", "chat" if name in KNOWN_CONTAINER_NOTES else "app",
            "review", False, label, desc, REVIEW_HINT, child["size"],
        ))

    # Application Support children.
    for child in measurements.get("appsupport", []):
        p: Path = child["path"]
        name = child["name"]
        if child["size"] < 20 * 1024 * 1024:
            continue
        if name == "MobileSync":
            # A deeper backup item is added below; avoid double counting.
            continue
        label = _friendly_app_support(name)
        desc = "应用写入的持久化数据，可能包含账号、模型、扩展或下载内容。"
        if name == "Google":
            desc = "Chrome 配置、扩展、离线模型等持久数据；不是缓存，不能整体删除。"
        elif name == "Steam":
            desc = "Steam 游戏与下载数据。请在 Steam 内卸载不玩的游戏。"
        elif name in ("Code", "Qoder", "QoderCN", "TRAE SOLO CN"):
            desc = "编辑器的扩展、工作区状态与模型数据；清理前请先关闭对应应用。"
        items.append(_base_item(
            p, "appsupport", "app", "review", False,
            label, desc, REVIEW_HINT, child["size"],
        ))

    # User caches.
    for child in measurements.get("caches", []):
        p: Path = child["path"]
        name = child["name"]
        if child["size"] < 1 * 1024 * 1024:
            continue
        risk = classify_cache(name)
        if risk == "safe":
            desc = CACHE_NOTES.get(
                name,
                "可重建的缓存目录。删除后应用会在下次启动或安装时重新生成。",
            )
            hint = "可安全清理；建议先退出相关应用。"
        else:
            desc = "未列入安全白名单的缓存目录。它可能包含应用状态，因此不会自动删除。"
            hint = "报告只读；确认无影响后可在 Finder 中手动处理。"
        items.append(_base_item(
            p, "caches", "cache", risk, risk == "safe",
            f"{name} 缓存", desc, hint, child["size"],
        ))

    # Fixed catalog entries.
    fixed_sizes: Dict[str, int] = measurements.get("fixed", {})
    for spec in FIXED_PATHS:
        p: Path = spec["path"]
        key = spec["key"]
        size = fixed_sizes.get(str(p), 0)
        if not p.exists() and size == 0:
            continue
        if size < 1024 * 1024 and spec["risk"] == "safe":
            continue
        item = _base_item(
            p, spec["category"], spec["kind"], spec["risk"], spec["cleanable"],
            spec["label"], spec["description"], spec["hint"], size,
        )
        item["key"] = key
        if key == "mobile-backup":
            item["label"] = "iPhone / iPad 备份"
        items.append(item)

    # Docker: report both allocated (du) and sparse logical size.
    docker_raw = LIBRARY / "Containers" / "com.docker.docker" / "Data" / "vms" / "0" / "data" / "Docker.raw"
    docker_dir = LIBRARY / "Containers" / "com.docker.docker"
    if docker_dir.exists():
        snap = stat_snapshot(docker_raw)
        allocated = 0
        for child in measurements.get("containers", []):
            if child["name"] == "com.docker.docker":
                allocated = child["size"]
                break
        item = _base_item(
            docker_dir, "dev", "docker", "review", False,
            "Docker 虚拟磁盘",
            f"Docker.raw 逻辑大小 {human_bytes(snap['logical_size'])}，实际占用 {human_bytes(allocated)}。"
            "它是稀疏文件，不会立刻长到 1 TB。",
            "不要直接删除 Docker.raw。用 Docker Desktop 的磁盘清理或 docker system prune。",
            allocated,
        )
        item["logical_size"] = snap["logical_size"]
        item["docker_raw"] = str(docker_raw)
        items.append(item)

    # Deduplicate identical paths, keeping the first (more specific) item.
    seen = set()
    unique: List[dict] = []
    for item in items:
        if item["path"] in seen:
            continue
        seen.add(item["path"])
        unique.append(item)
    unique.sort(key=lambda x: x["bytes"], reverse=True)
    return unique


def build_categories(measurements: dict, items: List[dict]) -> List[dict]:
    containers = sum(c["size"] for c in measurements.get("containers", []))
    appsupport = sum(c["size"] for c in measurements.get("appsupport", []))
    caches = sum(c["size"] for c in measurements.get("caches", []))
    fixed = measurements.get("fixed", {})

    def fixed_size(key: str) -> int:
        p = next((s["path"] for s in FIXED_PATHS if s["key"] == key), None)
        return fixed.get(str(p), 0) if p else 0

    dev_extra = (
        fixed_size("pnpm-store") + fixed_size("xcode-derived")
        + fixed_size("npm-cache") + fixed_size("sim-cache")
    )
    appsupport += fixed_size("group-containers") + fixed_size("cloud-storage")
    system = (
        fixed_size("vm-swap") + fixed_size("var-folders") + fixed_size("library-appsupport")
        + fixed_size("library-caches") + fixed_size("metadata") + fixed_size("biome")
        + max(0, fixed_size("library-logs") - fixed_size("diagnostic"))
    )
    media = fixed_size("mail") + fixed_size("messages")
    user = fixed_size("downloads") + fixed_size("trash")
    # /Library/Logs is measured but intentionally has no per-item entry.
    other = fixed.get("/Library/Logs", 0)

    raw = [
        ("containers", containers),
        ("appsupport", appsupport),
        ("caches", caches),
        ("dev", dev_extra),
        ("system", system),
        ("media", media),
        ("user", user),
        ("other", max(0, other)),
    ]
    total = sum(v for _, v in raw)
    categories = []
    for key, value in raw:
        if value <= 0:
            continue
        meta = CATEGORY_META.get(key, {"label": "其他", "color": "#64748b", "order": 99})
        categories.append({
            "key": key,
            "label": meta["label"],
            "color": meta["color"],
            "bytes": value,
            "size_label": human_bytes(value),
            "percent": round(value / total * 100, 1) if total else 0,
        })
    return categories


def cleanable_paths(items: Iterable[dict]) -> List[str]:
    return [i["path"] for i in items if i.get("cleanable")]
