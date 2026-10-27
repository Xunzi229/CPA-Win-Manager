#!/usr/bin/env bash
# 安装前结束已有的管理器进程，再覆盖到本机应用程序目录。
set -euo pipefail

SRC="${1:?请传入 CPA Mac Manager.app 路径}"
if [[ ! -d "$SRC" ]]; then
    echo "找不到应用程序：$SRC" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
    if [[ -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
        PYTHON="$SCRIPT_DIR/.venv/bin/python"
    else
        PYTHON="python3"
    fi
fi

if [[ -w /Applications ]]; then
    DEST="/Applications/CPA Mac Manager.app"
else
    mkdir -p "${HOME}/Applications"
    DEST="${HOME}/Applications/CPA Mac Manager.app"
fi

"$PYTHON" - "$DEST" "${HOME}/Applications/开源软件管理器.app" "/Applications/开源软件管理器.app" <<'PY'
import os
import sys
from pathlib import Path
import psutil

apps = [Path(item) for item in sys.argv[1:] if Path(item).exists()]
protected = {os.getpid()}
try:
    protected.update(parent.pid for parent in psutil.Process().parents())
except psutil.Error:
    pass
targets = []
for process in psutil.process_iter(["pid", "exe"]):
    if process.pid in protected:
        continue
    try:
        exe = process.info.get("exe") or process.exe()
        if not exe:
            continue
        candidate = Path(exe).resolve()
        if any(candidate == app.resolve() or app.resolve() in candidate.parents for app in apps):
            targets.append(process)
    except (psutil.Error, OSError):
        continue
for process in targets:
    try:
        process.kill()
    except psutil.Error:
        pass
if targets:
    _, alive = psutil.wait_procs(targets, timeout=8)
    for process in alive:
        try:
            process.kill()
        except psutil.Error:
            pass
    print("已结束进程：" + ", ".join(str(process.pid) for process in targets))
else:
    print("没有需要结束的管理器进程。")
PY

rm -rf "$DEST" \
    "${HOME}/Applications/开源软件管理器.app" \
    "/Applications/开源软件管理器.app"
ditto "$SRC" "$DEST"
xattr -rd com.apple.quarantine "$DEST" >/dev/null 2>&1 || true
echo "已安装到 $DEST"
