"""Small, dependency-free helpers."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def human_bytes(num: float, decimals: int = 1) -> str:
    num = float(num or 0)
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    while abs(num) >= 1024 and idx < len(units) - 1:
        num /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(num)} B"
    return f"{num:.{decimals}f} {units[idx]}"


def item_id(path: Path, kind: str = "item") -> str:
    raw = f"{kind}:{path}".encode("utf-8", "surrogateescape")
    return hashlib.sha1(raw).hexdigest()[:12]


def display_path(path: Path, home: Path) -> str:
    try:
        rel = path.relative_to(home)
        return str(Path("~") / rel)
    except ValueError:
        return str(path)


def is_within(path: Path, root: Path) -> bool:
    """True when path is strictly inside root (not root itself)."""
    try:
        p = Path(os.path.realpath(str(path)))
        r = Path(os.path.realpath(str(root)))
        return p != r and r in p.parents
    except (OSError, ValueError):
        return False


def read_json(path: Path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def append_jsonl(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(data, ensure_ascii=False) + "\n")


def read_jsonl(path: Path, limit: Optional[int] = None) -> List[dict]:
    if not path.exists():
        return []
    rows: List[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    if limit:
        return rows[-limit:]
    return rows


def run(cmd: List[str], timeout: int = 60, cwd: Optional[Path] = None) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            text=True,
            errors="replace",
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    except OSError as exc:
        return 1, "", str(exc)


def du_one(path: Path, timeout: int = 180) -> int:
    """Allocated bytes of path using macOS du. Returns 0 when unavailable."""
    if not path.exists() and not path.is_symlink():
        return 0
    code, out, _ = run(["du", "-skx", str(path)], timeout=timeout)
    if not out:
        return 0
    first = out.splitlines()[0]
    try:
        kb = int(first.split("\t", 1)[0].strip())
        return max(0, kb) * 1024
    except (ValueError, IndexError):
        return 0


def _parse_du_output(out: str, result: Dict[str, int]) -> None:
    if not out:
        return
    for line in out.splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        try:
            result[parts[1]] = max(0, int(parts[0].strip())) * 1024
        except ValueError:
            continue


def du_many(paths: Iterable[Path], timeout: int = 240,
            cancel_event=None) -> Dict[str, int]:
    """Measure many paths in one du call. Keyed by the exact path string.

    The du subprocess is polled so a scan can cancel promptly; when cancelled,
    the partial result is returned and the caller discards the scan.
    """
    usable: List[str] = []
    for path in paths:
        if path.exists() or path.is_symlink():
            usable.append(str(path))
    if not usable:
        return {}
    # Chunk to stay comfortably under ARG_MAX even for huge cache directories.
    result: Dict[str, int] = {}
    chunk_size = 80
    for start in range(0, len(usable), chunk_size):
        if cancel_event is not None and cancel_event.is_set():
            return result
        chunk = usable[start:start + chunk_size]
        try:
            proc = subprocess.Popen(
                ["du", "-skx", "--"] + chunk,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
            )
        except OSError:
            continue
        deadline = time.time() + timeout
        out = ""
        try:
            while True:
                try:
                    out, _ = proc.communicate(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    if cancel_event is not None and cancel_event.is_set():
                        proc.terminate()
                        try:
                            out, _ = proc.communicate(timeout=2)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.communicate()
                        return result
                    if time.time() > deadline:
                        proc.kill()
                        proc.communicate()
                        break
        finally:
            if proc.poll() is None:
                proc.kill()
                try:
                    proc.communicate(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
        _parse_du_output(out, result)
    return result


def disk_usage(path: Path = Path("/System/Volumes/Data")) -> dict:
    """Match Finder/df accounting (statvfs overcounts APFS reserved space)."""
    target = str(path if path.exists() else "/")
    code, out, _ = run(["df", "-k", target], timeout=10)
    if code == 0 and out:
        lines = [line for line in out.splitlines() if line.strip()]
        if len(lines) >= 2:
            parts = lines[1].split()
            if len(parts) >= 5:
                try:
                    total = int(parts[1]) * 1024
                    used = int(parts[2]) * 1024
                    free = int(parts[3]) * 1024
                    percent = float(parts[4].rstrip("%"))
                    return {
                        "total": total,
                        "used": used,
                        "free": free,
                        "percent": round(percent, 1),
                    }
                except (ValueError, IndexError):
                    pass
    usage = shutil.disk_usage(target)
    total, used, free = usage.total, usage.used, usage.free
    percent = round(used / total * 100, 1) if total else 0.0
    return {"total": total, "used": used, "free": free, "percent": percent}


def stat_snapshot(path: Path) -> dict:
    try:
        st = path.stat()
        return {
            "mtime": datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds"),
            "logical_size": st.st_size,
        }
    except OSError:
        return {"mtime": None, "logical_size": 0}


def find_local_snapshots() -> List[str]:
    code, out, _ = run(["tmutil", "listlocalsnapshots", "/"], timeout=20)
    snapshots: List[str] = []
    if out:
        for line in out.splitlines():
            line = line.strip()
            if line and "com.apple.TimeMachine" in line:
                snapshots.append(line)
    return snapshots


def boot_time_iso() -> Optional[str]:
    code, out, _ = run(["sysctl", "-n", "kern.boottime"], timeout=5)
    # Example: { sec = 172..., usec = ... } ...
    if not out:
        return None
    try:
        sec = int(out.split("sec =", 1)[1].split(",", 1)[0].strip())
        return datetime.fromtimestamp(sec).astimezone().isoformat(timespec="seconds")
    except (ValueError, IndexError):
        return None
