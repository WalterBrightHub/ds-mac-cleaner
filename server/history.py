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
                                json.dumps(report, ensure_ascii=False, default=str),
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
    def _db_events(self, limit: int) -> List[dict]:
        events: List[dict] = []
        with closing(self._connect()) as conn, conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, created_at, hostname, macos, disk_total, disk_used,
                              disk_free, system_data_bytes, cleanable_bytes,
                              report->'summary', report->'top_consumers', trigger
                       FROM scans ORDER BY created_at DESC LIMIT %s""",
                    (limit,),
                )
                for row in cur.fetchall():
                    events.append({
                        "type": "scan",
                        "id": row[0],
                        "created_at": _iso(row[1]),
                        "hostname": row[2],
                        "macos": row[3],
                        "disk_total": row[4],
                        "disk_used": row[5],
                        "disk_free": row[6],
                        "system_data_bytes": row[7],
                        "cleanable_bytes": row[8],
                        "summary": row[9] or {},
                        "top_consumers": row[10] or [],
                        "trigger": row[11] or "manual",
                    })
                cur.execute(
                    """SELECT id, created_at, scan_id, freed_bytes, requested_bytes,
                              items, errors, dry_run, trigger
                       FROM cleanup_runs ORDER BY created_at DESC LIMIT %s""",
                    (limit,),
                )
                for row in cur.fetchall():
                    events.append({
                        "type": "cleanup",
                        "id": row[0],
                        "created_at": _iso(row[1]),
                        "scan_id": row[2],
                        "freed_bytes": row[3],
                        "requested_bytes": row[4],
                        "items": row[5] or [],
                        "errors": row[6] or [],
                        "dry_run": row[7],
                        "trigger": row[8] or "manual",
                    })
        return events

    def list_events(self, limit: int = 30) -> dict:
        local = read_jsonl(LOCAL_HISTORY, limit=limit)
        db_events: List[dict] = []
        connected = False
        error = None
        try:
            if not self._schema_ready:
                self.ensure_schema()
            db_events = self._db_events(limit)
            connected = True
        except Exception as exc:
            error = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
            self._last_error = error

        merged = {}
        for event in local + db_events:
            key = (event.get("type"), event.get("id"))
            if key not in merged:
                merged[key] = event
        events = list(merged.values())
        for event in events:
            event.setdefault("trigger", "manual")
        events.sort(key=lambda e: e.get("created_at", ""), reverse=True)
        events = events[:limit]
        return {
            "events": events,
            "source": "postgres" if connected else "local",
            "db_connected": connected,
            "db_error": error,
            "local_events": len(local),
        }

    def local_only(self, limit: int = 30) -> List[dict]:
        return read_jsonl(LOCAL_HISTORY, limit=limit)
