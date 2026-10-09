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

## 打包指南 (macOS)

在 macOS 终端中，项目提供了自动化一键打包脚本 `build_mac.sh`，支持打包为原生 `.app` 应用包、`.dmg` 磁盘映像以及独立 CLI 命令行可执行文件。

### 1. 一键全自动打包（推荐）

```bash
cd mac
chmod +x build_mac.sh

# 默认：打包生成原生应用 CPA Mac Manager.app 并生成 DMG 安装镜像
./build_mac.sh

# 仅打包原生应用包 (.app)
./build_mac.sh app

# 打包单文件独立命令行二进制 (dist/cpa-mac-manager)
./build_mac.sh cli

# 同时打包 .app, .dmg 和 cli 二进制
./build_mac.sh all
```

打包产物将输出在 `mac/dist/` 目录下：
- `dist/CPA Mac Manager.app`：macOS 原生应用程序。双击即可启动，也可直接拖拽至 `/Applications`。
- `dist/CPA-Mac-Manager-arm64.dmg`（或 `-x86_64.dmg`）：可用于分发分享的 DMG 安装映像。
- `dist/cpa-mac-manager`：单个免依赖命令行程序，适合在终端启动和查看后台输出。

### 2. 手动使用 PyInstaller 打包

如需手动定制打包参数，可使用以下命令：

```bash
cd mac
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt pyinstaller

# 方式 A：使用预配置的 spec 文件打包为 .app
pyinstaller --clean --noconfirm CPA_Mac_Manager.spec

# 方式 B：打包为单个命令行可执行程序
pyinstaller --clean --noconfirm --onefile --console --name "cpa-mac-manager" manager.py
```

---

## 使用与权限说明

1. **双击启动**：
   双击 `CPA Mac Manager.app` 运行后，程序会自动获取空闲端口并在默认浏览器弹出 Web 管理面板。
2. **解除 Gatekeeper 隔离（若有弹窗提示）**：
   自签名或未签名的第三方应用在首次打开时，macOS 可能会提示“已损坏，无法打开”或“无法验证开发者”。
   - 可以在「系统设置」->「隐私与安全性」中点击【仍要打开】。
   - 或者在终端执行命令解除隔离属性：
     ```bash
     sudo xattr -rd com.apple.quarantine "/Applications/CPA Mac Manager.app"
     ```
3. **后台管理地址**：
   运行后可在浏览器打开控制台管理各项服务与便携工具，配置文件与数据存储在 `~/Library/Application Support/CPA-Mac-Manager/`。
