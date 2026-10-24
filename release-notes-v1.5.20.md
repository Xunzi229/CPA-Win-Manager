## CPA 统一管理器 v1.5.20

本版本重点优化 Windows 免安装软件更新体验，支持安装前自动关闭目标目录内正在运行的程序，并新增同版本安装包本地缓存复用；同时完善 macOS 检查任务的排队调度。

### Windows：免安装软件安装与缓存优化
- **安装前自动关闭占用程序**：解压校验完成、开始覆盖文件前，自动检测并强制关闭从目标安装目录（含子目录）启动的运行程序，支持多轮扫描处理重拉起的子进程及文件句柄释放短暂重试，同时排除管理器自身与父进程且不影响其他路径下的同名程序，统一解决目标软件未退出导致的 `[WinError 5] 拒绝访问` 安装失败问题。
- **同版本安装包本地缓存复用**：下载并完成 SHA-256 校验后，将对应版本的安装包及元数据缓存在本地 `.download-cache/portable-packages`；若当次安装未成功，下次点击“安装 / 更新”同一版本与附件时直接校验并复用本地缓存包解压安装，无需重新请求远程下载；切换版本或缓存损坏时自动重新下载。
- **完善安装提示与日志**：安装确认弹窗更新为说明安装前自动关闭目标目录程序，安装日志输出已关闭进程的 PID 及缓存命中提示。

### macOS：任务队列优化
- **支持检查任务排队执行**：统一浏览器端与 Tauri 原生桌面端的任务队列调度，支持连续或批量触发检查任务时有序排队执行，并保留并发轮询进度及任务历史记录。

### 构建与发布
- 提供 Windows AMD64 / ARM64、macOS Apple Silicon arm64 / Intel x86_64 安装包及 SHA-256 校验清单。

### 下载
| 平台 | 下载文件 |
| --- | --- |
| Windows AMD64 | [ZIP](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.20/CPA-Unified-Manager-v1.5.20-windows-amd64.zip) |
| Windows ARM64 | [ZIP](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.20/CPA-Unified-Manager-v1.5.20-windows-arm64.zip) |
| macOS Apple Silicon（arm64） | [DMG](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.20/CPA-Mac-Manager-v1.5.20-darwin-arm64.dmg) · [ZIP](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.20/CPA-Mac-Manager-v1.5.20-darwin-arm64.zip) |
| macOS Intel（x86_64） | [DMG](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.20/CPA-Mac-Manager-v1.5.20-darwin-x86_64.dmg) · [ZIP](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.20/CPA-Mac-Manager-v1.5.20-darwin-x86_64.zip) |

校验文件：[`SHA256SUMS.txt`](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.20/SHA256SUMS.txt)

完整变更：[v1.5.19...v1.5.20](https://github.com/Xunzi229/CPA-Win-Manager/compare/v1.5.19...v1.5.20)
