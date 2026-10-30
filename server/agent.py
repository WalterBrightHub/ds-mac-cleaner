"""Install/remove a per-user LaunchAgent watchdog for the local server."""
from __future__ import annotations

import os
import plistlib
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from . import config
from .util import run

LAUNCHCTL = "/bin/launchctl" if Path("/bin/launchctl").exists() else "/usr/bin/launchctl"


class AgentManager:
    def status(self) -> dict:
        installed = config.AGENT_PLIST.is_file()
        loaded = False
        detail = ""
        if shutil.which("launchctl") or Path(LAUNCHCTL).exists():
            code, out, err = run(
                [LAUNCHCTL, "print", f"gui/{os.getuid()}/{config.AGENT_LABEL}"],
                timeout=8,
            )
            loaded = code == 0
            if code != 0:
                detail = (err or out or "").strip()[:300]
        return {
            "installed": installed,
            "loaded": loaded,
            "label": config.AGENT_LABEL,
            "plist_path": str(config.AGENT_PLIST),
            "runner": str(config.AGENT_RUNNER),
            "interval_seconds": 300,
            "detail": detail,
        }

    def install(self) -> dict:
        try:
            config.AGENT_DIR.mkdir(parents=True, exist_ok=True)
            config.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            if not config.AGENT_RUNNER.exists():
                return {"ok": False, "error": "agent-runner.sh 缺失，请重新安装项目"}
            os.chmod(config.AGENT_RUNNER, 0o755)
            payload = {
                "Label": config.AGENT_LABEL,
                "ProgramArguments": ["/bin/bash", str(config.AGENT_RUNNER)],
                "RunAtLoad": True,
                "StartInterval": 300,
                "WorkingDirectory": str(config.ROOT),
                "StandardOutPath": str(config.AGENT_LOG),
                "StandardErrorPath": str(config.AGENT_LOG),
                "EnvironmentVariables": {
                    "PATH": "/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                    "DSMC_PROJECT_DIR": str(config.ROOT),
                },
                "LimitLoadToSessionType": "Aqua",
            }
            fd, tmp = tempfile.mkstemp(prefix=".dsmc-agent-", suffix=".plist", dir=str(config.AGENT_DIR))
            try:
                with os.fdopen(fd, "wb") as fh:
                    plistlib.dump(payload, fh)
                os.replace(tmp, config.AGENT_PLIST)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)

            # bootout is best-effort; bootstrap replaces a stale registration.
            run([LAUNCHCTL, "bootout", f"gui/{os.getuid()}/{config.AGENT_LABEL}"], timeout=10)
            code, out, err = run(
                [LAUNCHCTL, "bootstrap", f"gui/{os.getuid()}", str(config.AGENT_PLIST)],
                timeout=15,
            )
            if code != 0:
                return {"ok": False, "error": (err or out or "launchctl bootstrap failed").strip()[:800],
                        "status": self.status()}
            run([LAUNCHCTL, "kickstart", "-k", f"gui/{os.getuid()}/{config.AGENT_LABEL}"], timeout=10)
            return {"ok": True, "status": self.status()}
        except PermissionError as exc:
            return {"ok": False, "error": f"没有权限写入 LaunchAgents：{exc}"}
        except Exception as exc:
            return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}

    def uninstall(self) -> dict:
        try:
            run([LAUNCHCTL, "bootout", f"gui/{os.getuid()}/{config.AGENT_LABEL}"], timeout=10)
            if config.AGENT_PLIST.exists():
                config.AGENT_PLIST.unlink()
            return {"ok": True, "status": self.status()}
        except PermissionError as exc:
            return {"ok": False, "error": f"没有权限删除 LaunchAgent：{exc}"}
        except Exception as exc:
            return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
