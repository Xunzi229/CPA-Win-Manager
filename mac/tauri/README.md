# CPA Mac Manager (Tauri 原生桌面端)

基于 **Tauri v2 + Rust + WebKit** 构建的 macOS 原生极简高性能客户端。

## 🌟 核心特性
- **独立交付**：原生 Rust + macOS WKWebView 窗口，包内附带业务组件，不依赖系统 Python。
- **复用 Mac 后端**：通过标准输入输出 JSON-lines 调用已有 Mac 安装、校验及服务管理逻辑，不启动 HTTP 服务或浏览器。
- **macOS 原生体验**：原生窗口毛玻璃背景、红黄绿三色交通灯按钮联动、Dock 集成与原生目录选择器。
- **已接入功能**：
  - 项目服务管理（CLIProxyAPI 与 CPA-Manager-Plus 状态监控、启停、重启）。
  - 使用系统默认浏览器打开管理后台（`/management.html`）；重新读取当前目录的实际端口并检查服务存活，未运行或不可达时明确报错。
  - 免安装软件（Portable）与安装向导软件（DMG/PKG）目录管理、一键全量更新检查。
  - 新版本提示、真实管理器更新检查与任务失败反馈。
  - HTTP / HTTPS 代理与 1~16 分块多线程并发下载配置。
  - 白底等宽黑体日志实时终端与进度指示条。

---

## 🛠️ 单独打包与运行

### 1. 开发调试
```bash
cd mac/tauri
npm install
./build.sh
npm run dev
```

### 2. 独立打包生成 macOS .app 与 .dmg
在 macOS 终端中运行：
```bash
cd mac/tauri
chmod +x build.sh
./build.sh
```
无 Apple 开发者证书时，可运行 `APPLE_SIGNING_IDENTITY=- ./build.sh` 为本地产物补齐 ad-hoc 签名；这不等同于 Apple 公证。
首次构建必须使用 `build.sh` 生成内置业务组件；已生成后可直接使用 npm 命令：
```bash
npm run build
```

打包完成后，产物输出在：
- `src-tauri/target/release/bundle/dmg/`：带背景与拖拽安装指令的原生 DMG 安装镜像。
- `src-tauri/target/release/bundle/macos/CPA Mac Manager.app`：独立原生应用程序包。

### 3. 交互回归验证

在仓库根目录运行 `node --test mac/tests/test_mac_frontend.cjs`，验证原生环境中的脚本初始化、页面切换、设置保存、任务查询与状态刷新。`build.sh` 会在打包前执行这些检查。

原生页面使用 Tauri IPC 读取状态和执行操作，不依赖本地 HTTP 服务。设置与分类查询的参数使用 snake_case，与页面字段一致。

服务与软件版本检查、下载安装和批量下载通过包内 `cpa-native-worker` 调用 `cpa_mac.backends`。Rust 负责任务生命周期、取消请求和原生配置更新；不会读写另一套 Python GUI 配置。下载校验与文件操作完成后才结束任务，不强杀安装中的组件。

原生弹窗使用 Tauri dialog 插件；输入信息使用应用内弹窗，避免 WKWebView 的 `alert/confirm/prompt` 无响应。构建脚本执行前端、Rust、Python 回归测试，并使用 PyInstaller 将业务组件打入 `.app`。

按钮行为、清理范围及尚不支持的功能见 [按钮检查记录](BUTTON_AUDIT.md)。

安装包表格的“最新发布版本”与“自动匹配附件”列直接提供原生下拉选择框，无需单独弹框。可加载更多历史发布，预发布开关控制列表；附件只列出当前 Mac 架构可用的 DMG、PKG、ZIP。默认跟随最新发布并自动匹配，手选版本或附件即时持久化保存。安装和批量下载重新校验所选发布及附件，失效时明确报错，不会静默替换。批量只下载高于本地版本的所选目标；手动安装可选择历史版本。GitHub API 限流时沿用设置的代理读取发布网页。

CPA-Manager-Plus 页面可见时每 5 秒独立读取管理员 Key，也可点击“重新读取 Key”。首次初始化的日志包含明文时自动保存至当前目录的 0600 私有文件；数据库只保存摘要，无法反推已丢失的 Key，重启已有服务也不会生成。录入已有 Key 使用密码输入框，仅保存本地副本，不重置服务凭据；复制调用 macOS 原生剪贴板，不依赖 WebKit 权限。

### 4. 本地应用识别与关联

原生版扫描 `/Applications`、`/System/Applications`、`~/Applications` 及其子目录，不进入应用内部扫描 helper。版本从 XML / 二进制 `Contents/Info.plist` 读取，优先 `CFBundleShortVersionString`，其次 `CFBundleVersion`；无可用版本显示“未知”，不填充占位版本。

添加安装包软件、加载页面或点击“刷新本地版本”时，按软件名、应用显示名 / bundle 名 / 文件名和仓库名做忽略大小写与分隔符的精确匹配。存在多个候选时不自动关联，可手动选择。已有手动关联优先，每次刷新重新读取其版本和路径；取消关联后不会立即自动重绑，重新手动关联可恢复。

在仓库根目录运行 `cargo test --manifest-path mac/tauri/src-tauri/Cargo.toml --lib installed_apps`，验证版本读取、自动匹配、重复应用、升级刷新、卸载与取消关联持久化。

### 5. GitHub 远程软件搜索

软件库勾选“搜索 GitHub”并输入关键词后，通过原生 Rust 后端调用 GitHub 仓库搜索 API（最多展示前 30 条，保留远程总数），使用设置中的代理配置；空关键词或未勾选时保留内置软件库及分类筛选。远程结果不受内置分类限制，也不代表项目一定提供 macOS 安装包。

API 返回 403 / 429 时，自动使用相同代理读取 GitHub 网页搜索第一页及总数，不打开外部浏览器、不绕过代理。网页也不可用时明确报错。

连接失败、超时、GitHub 限流或数据异常会明确显示错误，不会伪装为“未找到软件”。连续搜索只展示最后一次查询结果，开始新查询会清除旧的选中项。运行 `cargo test --manifest-path mac/tauri/src-tauri/Cargo.toml --lib github_search` 可验证远程响应、请求参数、代理及错误处理；打包前会执行全部 Rust 和前端回归测试。
