"""Verified updates for the manager itself."""
import html
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid
import zipfile

from cpa_manager.backends import cli as cli_backend
from cpa_manager.core.runtime import executable_architecture, windows_architecture

REPOSITORY = "https://github.com/Xunzi229/CPA-Win-Manager"
EXECUTABLE = "CPA-Unified-Manager.exe"


def latest_release(proxy):
    opener = cli_backend.network(proxy)
    page = cli_backend.read_text(opener, REPOSITORY + "/releases/latest")
    match = re.search(r'/Xunzi229/CPA-Win-Manager/releases/expanded_assets/([^"\s<>]+)', page)
    if not match or cli_backend.version_key(match[1]) is None:
        raise RuntimeError("无法识别管理器最新版本。")
    tag = match[1]
    assets = cli_backend.read_text(opener, REPOSITORY + "/releases/expanded_assets/" + tag)
    links = {html.unescape(link) for link in re.findall(
        r'href="(/Xunzi229/CPA-Win-Manager/releases/download/[^"<>]+)"', assets)}
    prefix = "/Xunzi229/CPA-Win-Manager/releases/download/" + tag + "/"
    filename = f"CPA-Unified-Manager-{tag}-windows-{windows_architecture()}.zip"
    if prefix + filename not in links or prefix + "SHA256SUMS.txt" not in links:
        raise RuntimeError("发布页面缺少对应架构的管理器安装包或 SHA256SUMS.txt。")
    return tag, "https://github.com" + prefix + filename, "https://github.com" + prefix + "SHA256SUMS.txt"


def prepare_update(root, release, proxy, report):
    root = Path(root).resolve()
    stage = root / (".manager-update-" + uuid.uuid4().hex)
    stage.mkdir()
    _, url, sums_url = release
    opener = cli_backend.network(proxy)
    filename = url.rsplit("/", 1)[1]
    checksums = cli_backend.read_text(opener, sums_url)
    matches = re.findall(r"^([0-9a-fA-F]{64})\s+\*?(?:\./)?" + re.escape(filename) + r"\s*$", checksums, re.MULTILINE)
    if len(matches) != 1:
        raise RuntimeError("找不到安装包的唯一 SHA256 校验值。")
    archive = stage / "package.zip"
    digest = cli_backend.download(opener, url, archive, report)
    if digest.lower() != matches[0].lower():
        raise RuntimeError("管理器安装包 SHA256 校验失败，已取消更新。")
    with zipfile.ZipFile(archive) as package:
        entries = [entry for entry in package.infolist() if entry.filename == EXECUTABLE]
        if len(entries) != 1 or entries[0].file_size > 512 * 1024 * 1024:
            raise RuntimeError("安装包缺少唯一的管理器程序，或程序大小异常。")
        with package.open(entries[0]) as source, (stage / EXECUTABLE).open("wb") as destination:
            shutil.copyfileobj(source, destination)
    if executable_architecture(stage / EXECUTABLE) != windows_architecture():
        raise RuntimeError("管理器程序架构不匹配，已取消更新。")
    report(100, "下载及校验完成，准备重启管理器。")
    return stage


def launch_update(executable, stage):
    executable, stage = Path(executable).resolve(), Path(stage).resolve()
    if stage.parent != executable.parent or not stage.name.startswith(".manager-update-"):
        raise ValueError("更新暂存目录必须位于管理器目录中。")
    script = stage / "replace.ps1"
    script.write_text("""$ErrorActionPreference = 'Stop'
$target = $env:CPA_UPDATE_TARGET
$stage = $env:CPA_UPDATE_STAGE
$backup = Join-Path $stage 'previous.exe'
try {
    $owner = Get-Process -Id ([int]$env:CPA_UPDATE_PID) -ErrorAction SilentlyContinue
    if ($owner -and -not $owner.WaitForExit(60000)) { throw 'Manager exit timed out' }
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            [System.IO.File]::Replace((Join-Path $stage 'CPA-Unified-Manager.exe'), $target, $backup, $true)
            break
        } catch {
            if ($attempt -eq 29) { throw }
            Start-Sleep -Milliseconds 500
        }
    }
    Start-Process -FilePath $target -WorkingDirectory (Split-Path -Parent $target) -WindowStyle Hidden
} catch {
    $_ | Out-String | Set-Content -LiteralPath (Join-Path $stage 'update-error.log')
    if (Test-Path -LiteralPath $backup) {
        Copy-Item -LiteralPath $backup -Destination $target
        Start-Process -FilePath $target -WorkingDirectory (Split-Path -Parent $target) -WindowStyle Hidden
    }
}
""", encoding="utf-8-sig")
    environment = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1", CPA_UPDATE_TARGET=str(executable),
                       CPA_UPDATE_STAGE=str(stage), CPA_UPDATE_PID=str(os.getpid()))
    return subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive",
                             "-ExecutionPolicy", "Bypass", "-File", str(script)],
                            env=environment, cwd=executable.parent,
                            creationflags=subprocess.CREATE_NO_WINDOW)
