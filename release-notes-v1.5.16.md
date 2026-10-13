## CPA 统一管理器 v1.5.16

本版本重点优化交互体验，引入右下角轻量悬浮提醒（Toast），增加 CPA-Manager-Plus 管理员 Key 重置支持，完善系统默认浏览器跳转与解压目录编辑保护。

### 新增与优化
- **右下角悬浮提醒（Toast）**：使用右下角自动消失的悬浮提醒全面替换原本阻塞主界面的系统确认弹窗（包含操作成功、冲突拦截、报错提示等），无需手动点击确认，3秒后平滑淡出，支持快捷关闭。
- **管理员 Key 快捷重置**：CPA-Manager-Plus 卡片新增【重置 Key】功能，支持一键调用命令行重置并安全提取新管理员 Key，自动同步保存并填入界面。
- **系统默认浏览器跳转**：设置窗口“前往下载更新”、服务后台以及全局外部链接均通过系统原生接口在系统默认浏览器中打开，避免内嵌 WebKit/Tauri 拦截或窗口受限。
- **解压目录双击编辑保护**：解压目录输入框默认启用只读保护，防止误触修改；双击后激活编辑并支持失焦自动保存与恢复只读。

### Windows 与构建
- 持续提供 Windows（AMD64 / ARM64）与 macOS（Apple Silicon arm64 / Intel x86_64）四架构自动化构建发布与 SHA-256 校验清单。
- 全量自动化测试（前端 168 项、Python 73 项、Rust 23 项）均严格通过。

### 下载
| 平台 | 下载文件 |
| --- | --- |
| Windows AMD64 | [ZIP](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.16/CPA-Unified-Manager-v1.5.16-windows-amd64.zip) |
| Windows ARM64 | [ZIP](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.16/CPA-Unified-Manager-v1.5.16-windows-arm64.zip) |
| macOS Apple Silicon（arm64） | [DMG](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.16/CPA-Mac-Manager-v1.5.16-darwin-arm64.dmg) · [ZIP](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.16/CPA-Mac-Manager-v1.5.16-darwin-arm64.zip) |
| macOS Intel（x86_64） | [DMG](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.16/CPA-Mac-Manager-v1.5.16-darwin-x86_64.dmg) · [ZIP](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.16/CPA-Mac-Manager-v1.5.16-darwin-x86_64.zip) |

校验文件：[`SHA256SUMS.txt`](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.16/SHA256SUMS.txt)
