#!/bin/bash
# Build and install the launcher. Defaults to /Applications so Launchpad sees it.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
"$ROOT/scripts/build-app.sh"

SOURCE="$ROOT/dist/DS Mac Cleaner.app"
if [ -w "/Applications" ]; then
  DEST="/Applications/DS Mac Cleaner.app"
else
  mkdir -p "$HOME/Applications"
  DEST="$HOME/Applications/DS Mac Cleaner.app"
fi

echo "→ installing to $DEST"
rm -rf "$DEST"
ditto "$SOURCE" "$DEST"
xattr -dr com.apple.quarantine "$DEST" >/dev/null 2>&1 || true

LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
if [ -x "$LSREGISTER" ]; then
  "$LSREGISTER" -f "$DEST" >/dev/null 2>&1 || true
fi

echo "√ installed: $DEST"
echo "  已加入启动台（Launchpad）。首次打开请允许本地网络/文件访问。"

if [ "${DSMC_SKIP_AGENT:-0}" != "1" ]; then
  echo "→ installing always-on LaunchAgent"
  if "$ROOT/scripts/install-agent.sh" >/dev/null 2>&1; then
    echo "  常驻守护已安装：登录自启，意外退出后 5 分钟内恢复。"
  else
    echo "  ! 常驻守护安装失败，可稍后在 Web 的「常驻」页重试。"
  fi
fi
if [ "${1:-}" != "--no-open" ]; then
  open "$DEST"
fi
