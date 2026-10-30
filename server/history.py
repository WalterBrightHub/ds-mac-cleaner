"""History in PostgreSQL (Docker) with a local JSONL fallback.

The local fallback means the app remains useful when Docker Desktop is closed;
once PostgreSQL is available, new scans and cleanups are recorded there.
"""
from __future__ import annotations

import json
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from typing import List, Optional

from .config import (
    DB_HOST,
    DB_NAME,
    DB_PASSWORD,
    DB_PORT,
    DB_USER,
    LOCAL_HISTORY,
)
from .util import append_jsonl, read_jsonl

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS scans (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    hostname TEXT,
    macos TEXT,
    disk_total BIGINT,
    disk_used BIGINT,
    disk_free BIGINT,
    system_data_bytes BIGINT,
    cleanable_bytes BIGINT,
    trigger TEXT NOT NULL DEFAULT 'manual',
    report JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS cleanup_runs (
    id TEXT PRIMARY KEY,
    scan_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    dry_run BOOLEAN NOT NULL DEFAULT false,
    requested_bytes BIGINT NOT NULL DEFAULT 0,
    freed_bytes BIGINT NOT NULL DEFAULT 0,
    items JSONB NOT NULL DEFAULT '[]'::jsonb,
    errors JSONB NOT NULL DEFAULT '[]'::jsonb,
    trigger TEXT NOT NULL DEFAULT 'manual'
);
CREATE INDEX IF NOT EXISTS idx_scans_created_at ON scans (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cleanup_runs_created_at ON cleanup_runs (created_at DESC);
ALTER TABLE cleanup_runs DROP CONSTRAINT IF EXISTS cleanup_runs_scan_id_fkey;
ALTER TABLE scans ADD COLUMN IF NOT EXISTS trigger TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE cleanup_runs ADD COLUMN IF NOT EXISTS trigger TEXT NOT NULL DEFAULT 'manual';
"""


def _iso(value) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone().isoformat(timespec="seconds")
    return str(value or "")


class HistoryStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_error: Optional[str] = None
        self._schema_ready = False
        self._status_cache = None
        self._status_at = 0.0

    # ---------------------------------------------------------------- database
    def _connect(self):
        import psycopg2  # imported lazily so the host app can start without it

        return psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            connect_timeout=2,
            application_name="ds-mac-cleaner",
        )

    def ensure_schema(self) -> bool:
        try:
            with closing(self._connect()) as conn, conn:
                with conn.cursor() as cur:
                    cur.execute(SCHEMA_SQL)
                conn.commit()
            self._schema_ready = True
            self._last_error = None
            self._status_at = 0.0
            return True
        except Exception as exc:
            self._schema_ready = False
            self._last_error = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
            return False

    def status(self, force: bool = False) -> dict:
        if not force and self._status_cache and time.time() - self._status_at < 4:
            return self._status_cache
        try:
            with closing(self._connect()) as conn, conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT version()")
                    version = (cur.fetchone() or [""])[0]
                if not self._schema_ready:
                    self.ensure_schema()
            result = {
                "connected": True,
                "engine": "PostgreSQL",
                "version": str(version).split(",")[0],
                "host": f"{DB_HOST}:{DB_PORT}",
                "database": DB_NAME,
                "error": None,
            }
            self._status_cache = result
            self._status_at = time.time()
            return result
        except Exception as exc:
            self._last_error = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
            result = {
                "connected": False,
                "engine": "PostgreSQL",
                "version": None,
                "host": f"{DB_HOST}:{DB_PORT}",
                "database": DB_NAME,
                "error": self._last_error,
            }
            self._status_cache = result
            self._status_at = time.time()
            return result

    def record_scan(self, report: dict, trigger: str = "manual") -> None:
        summary = report.get("summary", {})
        disk = report.get("disk", {})
        event = {
            "type": "scan",
            "id": report["id"],
            "created_at": report["created_at"],
            "hostname": report.get("host", {}).get("hostname"),
            "macos": report.get("host", {}).get("macos"),
            "system_data_bytes": summary.get("system_data_estimate", 0),
            "cleanable_bytes": summary.get("cleanable_bytes", 0),
            "disk_total": disk.get("total", 0),
            "disk_used": disk.get("used", 0),
            "disk_free": disk.get("free", 0),
            "summary": summary,
            "top_consumers": report.get("top_consumers", [])[:5],
            "trigger": trigger,
        }
        with self._lock:
            append_jsonl(LOCAL_HISTORY, event)
            try:
                with closing(self._connect()) as conn, conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """INSERT INTO scans
                               (id, hostname, macos, disk_total, disk_used, disk_free,
                                system_data_bytes, cleanable_bytes, trigger, report)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                               ON CONFLICT (id) DO NOTHING""",
                            (
                                report["id"],
                                event.get("hostname"),
                                event.get("macos"),
                                event.get("disk_total"),
                                event.get("disk_used"),
                                event.get("disk_free"),
                                event.get("system_data_bytes"),
                                event.get("cleanable_bytes"),
                                trigger,
                                json.dumps(
                                    {k: v for k, v in report.items() if k != "items"},
                                    ensure_ascii=False, default=str,
                                ),
                            ),
                        )
                    conn.commit()
                self._schema_ready = True
            except Exception as exc:
                self._last_error = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__

    def record_cleanup(self, result: dict) -> None:
        event = {
            "type": "cleanup",
            "id": result["id"],
            "created_at": result["created_at"],
            "scan_id": result.get("scan_id"),
            "freed_bytes": result.get("freed_bytes", 0),
            "requested_bytes": result.get("requested_bytes", 0),
            "items": result.get("items", []),
            "errors": result.get("errors", []),
            "dry_run": bool(result.get("dry_run", False)),
            "trigger": result.get("trigger", "manual"),
        }
        with self._lock:
            append_jsonl(LOCAL_HISTORY, event)
            try:
                with closing(self._connect()) as conn, conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """INSERT INTO cleanup_runs
                               (id, scan_id, dry_run, requested_bytes, freed_bytes,
                                items, errors, trigger)
                               VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)
                               ON CONFLICT (id) DO NOTHING""",
                            (
                                result["id"],
                                result.get("scan_id"),
                                bool(result.get("dry_run", False)),
                                result.get("requested_bytes", 0),
                                result.get("freed_bytes", 0),
                                json.dumps(result.get("items", []), ensure_ascii=False, default=str),
                                json.dumps(result.get("errors", []), ensure_ascii=False, default=str),
                                result.get("trigger", "manual"),
                            ),
                        )
                    conn.commit()
                self._schema_ready = True
            except Exception as exc:
                self._last_error = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__

    # ------------------------------------------------------------------- reads
    def _db_page(self, page: int, page_size: int):
        with closing(self._connect()) as conn, conn:
            with conn.cursor() as cur:
                cur.execute("SELECT (SELECT count(*) FROM scans), (SELECT count(*) FROM cleanup_runs)")
                counts = cur.fetchone() or (0, 0)
                total = int(counts[0]) + int(counts[1])
                pages = max(1, (total + page_size - 1) // page_size)
                page = max(1, min(page, pages))
                offset = (page - 1) * page_size
                cur.execute(
                    """SELECT * FROM (
                           SELECT 'scan'::text AS type, id, created_at, trigger,
                                  hostname, macos, disk_total, disk_used, disk_free,
                                  system_data_bytes, cleanable_bytes,
                                  report->'summary' AS summary,
                                  report->'top_consumers' AS top_consumers,
                                  NULL::text AS scan_id, NULL::bigint AS freed_bytes,
                                  NULL::bigint AS requested_bytes, NULL::jsonb AS items,
                                  NULL::jsonb AS errors, NULL::boolean AS dry_run
                           FROM scans
                           UNION ALL
                           SELECT 'cleanup'::text, id, created_at, trigger,
                                  NULL::text, NULL::text, NULL::bigint, NULL::bigint,
                                  NULL::bigint, NULL::bigint, NULL::bigint,
                                  NULL::jsonb, NULL::jsonb,
                                  scan_id, freed_bytes, requested_bytes, items, errors, dry_run
                           FROM cleanup_runs
                       ) events
                       ORDER BY created_at DESC, id DESC
                       LIMIT %s OFFSET %s""",
                    (page_size, offset),
                )
                rows = cur.fetchall()
        events = []
        for row in rows:
            if row[0] == "scan":
                events.append({
                    "type": "scan",
                    "id": row[1],
                    "created_at": _iso(row[2]),
                    "trigger": row[3] or "manual",
                    "hostname": row[4],
                    "macos": row[5],
                    "disk_total": row[6],
                    "disk_used": row[7],
                    "disk_free": row[8],
                    "system_data_bytes": row[9],
                    "cleanable_bytes": row[10],
                    "summary": row[11] or {},
                    "top_consumers": row[12] or [],
                })
            else:
                events.append({
                    "type": "cleanup",
                    "id": row[1],
                    "created_at": _iso(row[2]),
                    "trigger": row[3] or "manual",
                    "scan_id": row[13],
                    "freed_bytes": row[14],
                    "requested_bytes": row[15],
                    "items": row[16] or [],
                    "errors": row[17] or [],
                    "dry_run": row[18],
                })
        return events, total, page, pages

    def _local_page(self, page: int, page_size: int):
        rows = read_jsonl(LOCAL_HISTORY)
        rows.reverse()
        total = len(rows)
        pages = max(1, (total + page_size - 1) // page_size)
        page = max(1, min(page, pages))
        start = (page - 1) * page_size
        events = rows[start:start + page_size]
        for event in events:
            event.setdefault("trigger", "manual")
        return events, total, page, pages

    def list_page(self, page: int = 1, page_size: int = 10) -> dict:
        page = max(1, int(page))
        page_size = max(1, min(50, int(page_size)))
        connected = False
        error = None
        try:
            if not self._schema_ready:
                self.ensure_schema()
            events, total, page, pages = self._db_page(page, page_size)
            connected = True
            source = "postgres"
        except Exception as exc:
            error = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
            self._last_error = error
            events, total, page, pages = self._local_page(page, page_size)
            source = "local"
        return {
            "events": events,
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": pages,
            "has_prev": page > 1,
            "has_next": page < pages,
            "source": source,
            "db_connected": connected,
            "db_error": error,
        }

    def list_events(self, limit: int = 30) -> dict:
        return self.list_page(1, limit)

    def local_only(self, limit: int = 30) -> List[dict]:
        return read_jsonl(LOCAL_HISTORY, limit=limit)
