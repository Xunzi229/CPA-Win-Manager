## 📦 CPA-Unified-Manager v1.5.14 更新说明

### ✨ 新增与优化
- **Windows 安装向导扫描日志静默优化**：
  - 后台每 30 秒自动扫描 Windows 已安装软件状态时进行变化比对，仅在首次加载或检测到软件安装记录发生变动（安装/卸载/版本变更）时输出日志，消除冗余刷屏。
- **macOS 软件管理器全面落地与功能移植 (`mac/`)**：
  - 新增完整的 macOS 本地管理套件，支持管理 CLIProxyAPI 和 CPA-Manager-Plus 服务，兼容 Darwin 架构下的免安装软件与 DMG/PKG 安装包。
  - **核心机制升级**：
    - 集成 10 分钟 Release 目录缓存机制（`CATALOG_CACHE_TTL = 600`），支持强制刷新。
    - 支持 GitHub API 403 / 429 速率限制自动无缝回退网页抓取。
    - 支持 1~16 并发分块下载配置与预发版本（Pre-release）筛选支持。
    - 联动后台地址，服务运行中一键直达 Web 控制台（`/management.html`）。
    - 页面采用 macOS 原生风格分段控制器、白底等宽黑体日志框及新版本提示小红点。
  - **macOS 自动化打包工具链**：
    - 新增 `mac/build_mac.sh` 脚本与 `mac/CPA_Mac_Manager.spec`，支持一键构建 macOS 原生应用包（`.app`）、DMG 磁盘镜像及独立 CLI 二进制可执行文件。
    - 内置应用图标自动高清矢量转制（`.png` -> `.icns`）与 Gatekeeper 隔离解除指引。

### 🧪 稳定性与测试
- Windows 单元测试全数通过（214 项）。
- macOS 核心模块单元测试全数通过（20 项）。

### 📥 下载与安装
| 系统架构 | 格式 | 资产文件名 | 说明 |
| :--- | :--- | :--- | :--- |
| **Windows x64 (amd64)** | ZIP | `CPA-Unified-Manager-v1.5.14-windows-amd64.zip` | 包含可执行程序，解压即用 |
| **Windows ARM64** | ZIP | `CPA-Unified-Manager-v1.5.14-windows-arm64.zip` | Windows 11 ARM 原生支持 |
| **macOS Apple Silicon (arm64)** | DMG | `CPA-Mac-Manager-v1.5.14-darwin-arm64.dmg` | M1/M2/M3/M4 系列 Mac 推荐，拖拽安装 |
| **macOS Apple Silicon (arm64)** | ZIP | `CPA-Mac-Manager-v1.5.14-darwin-arm64.zip` | 包含独立 .app 应用程序包 |
| **macOS Intel (x86_64)** | DMG | `CPA-Mac-Manager-v1.5.14-darwin-x86_64.dmg` | Intel 处理器 Mac 推荐，拖拽安装 |
| **macOS Intel (x86_64)** | ZIP | `CPA-Mac-Manager-v1.5.14-darwin-x86_64.zip` | 包含独立 .app 应用程序包 |

> 校验清单详见附件中的 `SHA256SUMS.txt`。Windows 解压后直接运行 `CPA-Unified-Manager.exe` 即可使用；macOS 双击 DMG 拖拽入“应用程序”文件夹即可使用。