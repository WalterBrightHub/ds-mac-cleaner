#!/usr/bin/env python3
"""DS Mac Cleaner local web server (Python standard library only)."""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from server import config  # type: ignore
    from server.cleanup import CleanupError, permission_probe, run_cleanup  # type: ignore
    from server.history import HistoryStore  # type: ignore
    from server.scanner import ScanCancelled, run_scan  # type: ignore
    from server.util import atomic_write_json, read_json, run  # type: ignore
else:
    from . import config
    from .cleanup import CleanupError, permission_probe, run_cleanup
    from .history import HistoryStore
    from .scanner import ScanCancelled, run_scan
    from .util import atomic_write_json, read_json, run

TOKEN = secrets.token_urlsafe(28)
SCAN_ID_RE = re.compile(r"^scan-[A-Za-z0-9._-]{3,80}$")
TASK_ID_RE = re.compile(r"^task-[A-Za-z0-9._-]{3,80}$")


def find_docker() -> Optional[str]:
    """Finder-launched apps do not inherit the user's shell PATH."""
    candidates = [
        shutil.which("docker"),
        "/usr/local/bin/docker",
        "/opt/homebrew/bin/docker",
        "/Applications/Docker.app/Contents/Resources/bin/docker",
    ]
    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}


class TaskManager:
    def __init__(self, history: HistoryStore) -> None:
        self.history = history
        self._lock = threading.Lock()
        self._tasks: Dict[str, dict] = {}
        self._active_scan: Optional[str] = None
        self._cancel_events: Dict[str, threading.Event] = {}

    def _new_task(self, kind: str, trigger: str = "manual") -> dict:
        task_id = f"task-{int(time.time())}-{secrets.token_hex(4)}"
        task = {
            "id": task_id,
            "kind": kind,
            "trigger": trigger,
            "status": "running",
            "progress": 0,
            "stage": "准备中",
            "created_at": time.time(),
            "error": None,
            "result": None,
        }
        with self._lock:
            self._tasks[task_id] = task
            # Keep memory bounded during a long-running local app.
            if len(self._tasks) > 30:
                old = sorted(self._tasks.values(), key=lambda t: t["created_at"])[:10]
                for item in old:
                    if item["status"] != "running":
                        self._tasks.pop(item["id"], None)
        return task

    def _update(self, task: dict, **kwargs) -> None:
        with self._lock:
            task.update(kwargs)

    def get(self, task_id: str) -> Optional[dict]:
        with self._lock:
            task = self._tasks.get(task_id)
            return dict(task) if task else None

    def active_scan(self) -> Optional[dict]:
        with self._lock:
            if not self._active_scan:
                return None
            task = self._tasks.get(self._active_scan)
            return dict(task) if task else None

    def start_scan(self, trigger: str = "manual") -> dict:
        with self._lock:
            if self._active_scan:
                task = self._tasks.get(self._active_scan)
                if task and task["status"] == "running":
                    return dict(task)
        task = self._new_task("scan", trigger=trigger)
        cancel_event = threading.Event()
        with self._lock:
            self._active_scan = task["id"]
            self._cancel_events[task["id"]] = cancel_event
        thread = threading.Thread(
            target=self._run_scan, args=(task, cancel_event), daemon=True, name="dsmc-scan",
        )
        thread.start()
        return dict(task)

    def cancel_scan(self, task_id: Optional[str] = None) -> Optional[dict]:
        with self._lock:
            active_id = self._active_scan
            if not active_id or (task_id and task_id != active_id):
                return None
            task = self._tasks.get(active_id)
            event = self._cancel_events.get(active_id)
            if not task or task.get("status") != "running":
                return None
            if event is not None:
                event.set()
            task["cancel_requested"] = True
            task["stage"] = "正在中断扫描…"
            return dict(task)

    def _prune_scan_reports(self, keep: int = 60) -> None:
        """Keep history summaries in the DB but bound full JSON report files."""
        try:
            files = sorted(
                config.SCANS_DIR.glob("scan-*.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            for old in files[keep:]:
                try:
                    old.unlink()
                except OSError:
                    pass
        except OSError:
            pass

    def _run_scan(self, task: dict, cancel_event: threading.Event) -> None:
        def progress(pct: int, stage: str) -> None:
            self._update(task, progress=max(1, min(99, pct)), stage=stage)

        try:
            report = run_scan(progress, cancel_event)
            report["trigger"] = task.get("trigger", "manual")
            config.SCANS_DIR.mkdir(parents=True, exist_ok=True)
            atomic_write_json(config.SCANS_DIR / f"{report['id']}.json", report)
            atomic_write_json(config.LATEST_SCAN, report)
            self._prune_scan_reports(keep=60)
            try:
                self.history.record_scan(report, trigger=task.get("trigger", "manual"))
            except Exception:
                pass
            self._update(task, status="done", progress=100, stage="扫描完成", result=report)
        except ScanCancelled:
            self._update(task, status="cancelled", progress=100, stage="扫描已中断",
                         error=None, result=None)
        except Exception as exc:
            self._update(task, status="error", progress=100, stage="扫描失败",
                         error=f"{exc.__class__.__name__}: {exc}")
        finally:
            with self._lock:
                if self._active_scan == task["id"]:
                    self._active_scan = None
                self._cancel_events.pop(task["id"], None)

    def start_cleanup(self, report: dict, item_ids, dry_run: bool = False) -> dict:
        task = self._new_task("cleanup", trigger="manual")
        thread = threading.Thread(
            target=self._run_cleanup, args=(task, report, item_ids, dry_run),
            daemon=True, name="dsmc-cleanup",
        )
        thread.start()
        return dict(task)

    def _run_cleanup(self, task: dict, report: dict, item_ids, dry_run: bool) -> None:
        try:
            self._update(task, progress=20, stage="校验清理白名单")
            result = run_cleanup(report, item_ids, dry_run=dry_run)
            result["trigger"] = "manual"
            self._update(task, progress=85, stage="写入历史记录")
            try:
                self.history.record_cleanup(result)
            except Exception:
                pass
            self._update(task, status="done", progress=100, stage="清理完成", result=result)
        except CleanupError as exc:
            self._update(task, status="error", progress=100, stage="清理被拒绝", error=str(exc))
        except Exception as exc:
            self._update(task, status="error", progress=100, stage="清理失败",
                         error=f"{exc.__class__.__name__}: {exc}")


class App:
    def __init__(self) -> None:
        config.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        config.SCANS_DIR.mkdir(parents=True, exist_ok=True)
        self.history = HistoryStore()
        self.tasks = TaskManager(self.history)
        self.started = time.time()
        self._db_start_lock = threading.Lock()

    def latest(self) -> Optional[dict]:
        report = read_json(config.LATEST_SCAN)
        if not report:
            return None
        report["age_seconds"] = max(0, int(time.time() - float(report.get("started_at", time.time()))))
        return report

    def get_scan(self, scan_id: str) -> Optional[dict]:
        if not SCAN_ID_RE.match(scan_id or ""):
            return None
        return read_json(config.SCANS_DIR / f"{scan_id}.json")

    def all_paths(self) -> set:
        latest = read_json(config.LATEST_SCAN) or {}
        return {item.get("path") for item in latest.get("items", [])}

    def start_db(self) -> dict:
        docker = find_docker()
        if not docker:
            return {"ok": False, "error": "未找到 docker 命令。请先安装并启动 Docker Desktop。"}
        with self._db_start_lock:
            code, out, err = run(
                [docker, "compose", "--project-directory", str(config.ROOT),
                 "-f", str(config.COMPOSE_FILE), "up", "-d", "db"],
                timeout=150, cwd=config.ROOT,
            )
            if code != 0:
                detail = (err or out or "").strip()
                return {"ok": False, "error": f"启动数据库失败：{detail[-800:]}"}
            deadline = time.time() + 45
            while time.time() < deadline:
                if self.history.ensure_schema():
                    return {"ok": True, "status": self.history.status()}
                time.sleep(1.5)
            status = self.history.status()
            return {"ok": False, "error": status.get("error") or "数据库启动超时", "status": status}


APP: Optional[App] = None


def json_bytes(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = f"DSMacCleaner/{config.VERSION}"
    protocol_version = "HTTP/1.1"

    # -------------------------------------------------------------- primitives
    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, status: int, body: bytes, content_type: str, extra: Optional[dict] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload, status: int = 200) -> None:
        self._send(status, json_bytes(payload), "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json({"ok": False, "error": message}, status)

    def _read_json(self) -> dict:
        raw = getattr(self, "_raw_body", b"") or b"{}"
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON object required")
        return data

    def _consume_body(self) -> bool:
        """Read the request body exactly once and preserve keep-alive framing.

        Every POST must consume its Content-Length, even endpoints that ignore
        the body; otherwise the unread bytes are parsed as the next request.
        """
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._error(400, "无效的 Content-Length")
            self.close_connection = True
            return False
        if length < 0 or length > 1_000_000:
            self._error(413, "请求体过大")
            self.close_connection = True
            return False
        self._raw_body = self.rfile.read(length) if length else b""
        return True

    def _check_token(self) -> bool:
        supplied = self.headers.get("X-DSMC-Token", "")
        if not secrets.compare_digest(supplied, TOKEN):
            self._error(HTTPStatus.FORBIDDEN, "无效的本地会话令牌，请刷新页面")
            return False
        origin = self.headers.get("Origin")
        if origin:
            host = self.headers.get("Host", "")
            allowed = {f"http://{host}", f"http://127.0.0.1:{config.PORT}", f"http://localhost:{config.PORT}"}
            if origin not in allowed:
                self._error(HTTPStatus.FORBIDDEN, "拒绝跨站请求")
                return False
        return True

    # -------------------------------------------------------------------- GET
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/api/health":
            self._json({
                "ok": True,
                "version": config.VERSION,
                "uptime_seconds": int(time.time() - APP.started),
                "hostname": socket.gethostname().replace(".local", ""),
                "db": APP.history.status(),
                "docker_available": bool(find_docker()),
                "platform": sys.platform,
                "arch": platform.machine(),
                "translated": bool(os.environ.get("DSMC_TRANSLATED") == "1"),
                "port": config.PORT,
            })
            return
        if path == "/api/latest":
            latest = APP.latest()
            self._json({"ok": True, "scan": latest})
            return
        if path == "/api/history":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                page = max(1, int(query.get("page", ["1"])[0]))
            except ValueError:
                page = 1
            try:
                raw_size = query.get("page_size", query.get("limit", ["10"]))[0]
                page_size = max(1, min(50, int(raw_size)))
            except ValueError:
                page_size = 10
            payload = APP.history.list_page(page, page_size)
            payload["ok"] = True
            self._json(payload)
            return
        if path == "/api/db/status":
            self._json({"ok": True, "db": APP.history.status()})
            return
        if path == "/api/scans/active":
            self._json({"ok": True, "task": APP.tasks.active_scan()})
            return
        if path == "/api/permissions":
            checks = permission_probe()
            self._json({
                "ok": True,
                "writable": all(item["writable"] for item in checks),
                "checks": checks,
            })
            return
        if path.startswith("/api/tasks/"):
            task_id = path.rsplit("/", 1)[-1]
            if not TASK_ID_RE.match(task_id):
                self._error(404, "任务不存在")
                return
            task = APP.tasks.get(task_id)
            if not task:
                self._error(404, "任务不存在或已过期")
                return
            self._json({"ok": True, "task": task})
            return
        if path.startswith("/api/scans/") and path.endswith("/export"):
            scan_id = path[len("/api/scans/"):-len("/export")]
            report = APP.get_scan(scan_id)
            if not report:
                self._error(404, "扫描报告不存在")
                return
            body = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8",
                       {"Content-Disposition": f'attachment; filename="{scan_id}.json"'})
            return
        if path in ("/", "/index.html"):
            self._serve_index()
            return
        if path.startswith("/static/"):
            self._serve_static(path[len("/static/"):])
            return
        if path == "/favicon.ico":
            self._serve_static("favicon.svg")
            return
        self._error(404, "Not found")

    def _serve_index(self) -> None:
        try:
            html = (config.WEB_DIR / "index.html").read_text(encoding="utf-8")
        except OSError:
            self._error(500, "Web UI 文件缺失")
            return
        html = html.replace("__DSMC_TOKEN__", TOKEN).replace("__DSMC_VERSION__", config.VERSION)
        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

    def _serve_static(self, rel: str) -> None:
        # Reject encoded traversal and only serve plain files from web/.
        rel = urllib.parse.unquote(rel)
        if ".." in Path(rel).parts or rel.startswith("/"):
            self._error(403, "Forbidden")
            return
        target = (config.WEB_DIR / rel).resolve()
        try:
            target.relative_to(config.WEB_DIR.resolve())
        except ValueError:
            self._error(403, "Forbidden")
            return
        if not target.is_file():
            self._error(404, "Not found")
            return
        suffix = target.suffix.lower()
        ctype = MIME.get(suffix) or mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        try:
            body = target.read_bytes()
        except OSError:
            self._error(500, "无法读取静态文件")
            return
        self._send(200, body, ctype, {"Cache-Control": "no-cache"})

    # ------------------------------------------------------------------- POST
    def do_POST(self):
        if not self._consume_body():
            return
        if not self._check_token():
            return
        path = urllib.parse.urlparse(self.path).path
        try:
            if path.startswith("/api/scans/") and path.endswith("/cancel"):
                task_id = path[len("/api/scans/"):-len("/cancel")]
                if not TASK_ID_RE.match(task_id):
                    self._error(404, "扫描任务不存在")
                    return
                task = APP.tasks.cancel_scan(task_id)
                if not task:
                    self._error(409, "没有可中断的扫描")
                    return
                self._json({"ok": True, "task": task}, 202)
                return
            if path == "/api/scans":
                data = self._read_json()
                trigger = data.get("trigger", "manual")
                if trigger not in ("manual", "launch"):
                    trigger = "manual"
                task = APP.tasks.start_scan(trigger=trigger)
                self._json({"ok": True, "task": task}, 202)
                return
            if path == "/api/cleanups":
                data = self._read_json()
                if data.get("confirm") != "DELETE":
                    self._error(400, "需要输入 DELETE 以确认永久删除缓存")
                    return
                scan_id = str(data.get("scan_id", ""))
                report = APP.get_scan(scan_id)
                if not report:
                    self._error(404, "找不到对应的扫描报告，请重新扫描")
                    return
                item_ids = data.get("item_ids")
                if not isinstance(item_ids, list) or not item_ids:
                    self._error(400, "没有选择任何项目")
                    return
                task = APP.tasks.start_cleanup(report, item_ids, dry_run=bool(data.get("dry_run", False)))
                self._json({"ok": True, "task": task}, 202)
                return
            if path == "/api/db/start":
                result = APP.start_db()
                self._json(result, 200 if result.get("ok") else 500)
                return
            if path == "/api/reveal":
                data = self._read_json()
                target = str(data.get("path", ""))
                if target not in APP.all_paths():
                    self._error(403, "只能显示最近一次扫描中的路径")
                    return
                if not Path(target).exists():
                    self._error(404, "路径已不存在")
                    return
                code, out, err = run(["open", "-R", target], timeout=10)
                if code != 0:
                    self._error(500, err or "无法打开 Finder")
                    return
                self._json({"ok": True})
                return
            if path == "/api/open-storage-settings":
                code, out, err = run(
                    ["open", "x-apple.systempreferences:com.apple.settings.Storage"], timeout=10,
                )
                if code != 0:
                    run(["open", "-b", "com.apple.systempreferences"], timeout=10)
                self._json({"ok": True})
                return
            self._error(404, "Not found")
        except json.JSONDecodeError:
            self._error(400, "请求不是合法 JSON")
        except ValueError as exc:
            self._error(400, str(exc))
        except Exception as exc:
            self.log_message("POST %s failed: %s", path, exc)
            self._error(500, f"{exc.__class__.__name__}: {exc}")


def main() -> int:
    global APP
    parser = argparse.ArgumentParser(description="DS Mac Cleaner local server")
    parser.add_argument("--port", type=int, default=config.PORT)
    parser.add_argument("--host", default=config.HOST)
    parser.add_argument("--open", action="store_true", help="open the browser after startup")
    args = parser.parse_args()
    config.PORT = args.port
    config.HOST = args.host

    APP = App()
    # Try the DB in the background; never block the UI on Docker.
    threading.Thread(target=APP.history.ensure_schema, daemon=True).start()

    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        print(f"Cannot bind {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 2
    server.daemon_threads = True
    url = f"http://{args.host}:{args.port}"
    print(f"DS Mac Cleaner {config.VERSION} listening on {url}", flush=True)
    print(f"Runtime: {config.RUNTIME_DIR}", flush=True)
    if args.open or config.OPEN_BROWSER:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
