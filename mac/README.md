# CPA Mac 管理器

macOS 上管理 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) 和 [CPA-Manager-Plus](https://github.com/seakee/CPA-Manager-Plus)，也可以安装带 GitHub Release 的免安装软件和 dmg/pkg。

## 功能

- 按本机架构下载 darwin 安装包，校验 SHA256，安装、启动、停止、重启。已有 `config.yaml` / `config.json` 不会被覆盖。启动后可打开本机管理页面。
- 免安装软件支持 zip、tar.gz、tar.xz、tar.bz2 和单文件，优先选择 darwin 且架构匹配的附件。每行可以单独检查或安装，有新版本时标出可更新。
- 安装包下载 dmg/pkg 后用系统打开。下载目录里已有且校验通过的文件会直接复用。
- 软件库可按名称筛选，也可以搜索 GitHub 后直接加入。正在下载时可以取消。
- 代理、窗口大小和软件列表保存在 `~/Library/Application Support/CPA-Mac-Manager/settings.json`，权限为仅当前用户可读写。

Windows 版的 EXE/MSI 安装向导、注册表关联、DPAPI 和管理器自更新不在这个目录里。

## 运行

需要 macOS 和 Python 3.10+。运行后会在浏览器打开管理页面。

```bash
cd mac
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manager.py
```

```bash
.venv/bin/python -m unittest discover -s tests -q
```
