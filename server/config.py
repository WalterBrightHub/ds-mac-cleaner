"""Runtime configuration for DS Mac Cleaner."""
from __future__ import annotations

import os
from pathlib import Path

VERSION = "1.0.0"
APP_NAME = "DS Mac Cleaner"
APP_NAME_ZH = "DS 麦清理"

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
RUNTIME_DIR = Path(os.environ.get("DSMC_RUNTIME_DIR", ROOT / ".runtime"))
SCANS_DIR = RUNTIME_DIR / "scans"
LOCAL_HISTORY = RUNTIME_DIR / "history.jsonl"
LATEST_SCAN = RUNTIME_DIR / "latest_scan.json"

HOME = Path.home()
LIBRARY = HOME / "Library"

HOST = os.environ.get("DSMC_HOST", "127.0.0.1")
PORT = int(os.environ.get("DSMC_PORT", "8787"))
OPEN_BROWSER = os.environ.get("DSMC_OPEN_BROWSER", "0") == "1"

DB_HOST = os.environ.get("DSMC_DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("DSMC_DB_PORT", "55432"))
DB_NAME = os.environ.get("DSMC_DB_NAME", "dsmac")
DB_USER = os.environ.get("DSMC_DB_USER", "dsmac")
DB_PASSWORD = os.environ.get("DSMC_DB_PASSWORD", "dsmac_local_dev")
DB_CONNECT_TIMEOUT = int(os.environ.get("DSMC_DB_TIMEOUT", "2"))

COMPOSE_FILE = ROOT / "docker-compose.yml"

# Only these roots may ever be deleted from. Individual scans can only clean
# catalog entries that also carry risk == "safe".
SAFE_DELETE_ROOTS = [
    LIBRARY / "Caches",
    LIBRARY / "Logs" / "DiagnosticReports",
    LIBRARY / "Developer" / "Xcode" / "DerivedData",
    LIBRARY / "Developer" / "CoreSimulator" / "Caches",
    HOME / ".npm" / "_cacache",
]

# Human-readable names for common app containers. Only used for labels.
CONTAINER_NAMES = {
    "com.tencent.qq": "QQ",
    "com.tencent.xinWeChat": "微信",
    "com.docker.docker": "Docker Desktop",
    "5ZSL2CJU2T.com.dingtalk.mac": "钉钉",
    "com.tencent.QQMusicMac": "QQ 音乐",
    "com.tencent.WeWorkMac": "企业微信",
    "com.kingsoft.wpsoffice.mac": "WPS Office",
    "com.tencent.meeting": "腾讯会议",
    "com.microsoft.Word": "Microsoft Word",
    "com.microsoft.Excel": "Microsoft Excel",
    "com.microsoft.Powerpoint": "Microsoft PowerPoint",
    "com.apple.geod": "系统定位服务",
    "com.netease.163music": "网易云音乐",
    "com.xunlei.mvideo": "迅雷",
    "com.firecore.infuse": "Infuse",
}

APP_SUPPORT_NAMES = {
    "Google": "Google（Chrome 等）",
    "Qoder": "Qoder",
    "QoderCN": "Qoder CN",
    "TRAE SOLO CN": "TRAE SOLO CN",
    "Code": "Visual Studio Code",
    "微信开发者工具": "微信开发者工具",
    "Steam": "Steam",
    "Quark": "夸克",
    "QClaw": "QClaw",
    "Caches": "应用共享缓存",
    "Codex": "Codex",
    "WeType": "微信输入法",
    "apifox": "Apifox",
    "Docker Desktop": "Docker Desktop",
    "MobileSync": "iPhone / iPad 备份",
    "Adobe": "Adobe",
}
