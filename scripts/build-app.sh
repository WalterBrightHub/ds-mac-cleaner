#!/bin/bash
# Build a self-contained macOS .app launcher inside dist/.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

APP="$ROOT/dist/DS Mac Cleaner.app"
CONTENTS="$APP/Contents"
VERSION="$(python3 - <<'PY'
import re
from pathlib import Path
text = Path('server/config.py').read_text()
print(re.search(r'VERSION = "([^"]+)"', text).group(1))
PY
)"

PYTHON="$ROOT/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  PYTHON="$(command -v python3)"
fi
if ! "$PYTHON" -c 'import PIL' >/dev/null 2>&1; then
  echo "Pillow is required to build the icon. Run:" >&2
  echo "  .venv/bin/pip install Pillow==10.4.0" >&2
  exit 1
fi

echo "→ generating icon"
"$PYTHON" scripts/make-icon.py >/dev/null

echo "→ assembling $APP"
rm -rf "$APP"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources"
cp "$ROOT/dist/icon-build/AppIcon.icns" "$CONTENTS/Resources/AppIcon.icns"

# Inject the project path into the launcher.
"$PYTHON" - "$ROOT" "$CONTENTS/MacOS/DSMacCleaner" <<'PY'
import sys
from pathlib import Path
root, target = sys.argv[1], Path(sys.argv[2])
template = (Path(root) / "launcher" / "DSMacCleaner.sh").read_text()
target.write_text(template.replace("__PROJECT_DIR__", root))
target.chmod(0o755)
PY

cat > "$CONTENTS/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>zh_CN</string>
  <key>CFBundleExecutable</key><string>DSMacCleaner</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleIdentifier</key><string>com.deepseek.dsmaccleaner</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>DS Mac Cleaner</string>
  <key>CFBundleDisplayName</key><string>DS 麦清理</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleVersion</key><string>${VERSION}</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSArchitecturePriority</key>
  <array>
    <string>arm64</string>
    <string>x86_64</string>
  </array>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>DS Mac Cleaner · local-only storage analysis</string>
</dict>
</plist>
PLIST
printf 'APPL????' > "$CONTENTS/PkgInfo"

if command -v codesign >/dev/null 2>&1; then
  codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true
fi

echo "√ built: $APP"
