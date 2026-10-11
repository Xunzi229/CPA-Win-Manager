# CPA Mac Manager (Tauri 原生桌面端)

基于 **Tauri v2 + Rust + WebKit** 构建的 macOS 原生极简高性能客户端。

## 🌟 核心特性
- **极致轻量**：安装包仅约 5~8 MB（对比传统 Python 打包体积减小 80%+）。
- **秒级启动**：原生 Rust + macOS 系统 WKWebView，零内存解释器开销，启动毫秒级响应。
- **macOS 原生体验**：原生窗口毛玻璃背景、红黄绿三色交通灯按钮联动、Dock 集成与原生目录选择器。
- **全功能对齐**：
  - 项目服务管理（CLIProxyAPI 与 CPA-Manager-Plus 状态监控、启停、重启）。
  - 一键打开管理后台（`/management.html`）。
  - 免安装软件（Portable）与安装向导软件（DMG/PKG）目录管理、一键全量更新检查。
  - 新版本红点提示与 10 分钟 Release 缓存机制。
  - HTTP / HTTPS 代理与 1~16 分块多线程并发下载配置。
  - 白底等宽黑体日志实时终端与进度指示条。

---

## 🛠️ 单独打包与运行

### 1. 开发调试
```bash
cd mac/tauri
npm install
npm run dev
```

### 2. 独立打包生成 macOS .app 与 .dmg
在 macOS 终端中运行：
```bash
cd mac/tauri
chmod +x build.sh
./build.sh
```
或者直接使用 npm 命令：
```bash
npm run build
```

打包完成后，产物输出在：
- `src-tauri/target/release/bundle/dmg/`：带背景与拖拽安装指令的原生 DMG 安装镜像。
- `src-tauri/target/release/bundle/macos/CPA Mac Manager.app`：独立原生应用程序包。