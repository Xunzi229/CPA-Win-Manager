## CPA 统一管理器 v1.5.17

本版本重点增加历史备份数量感知展示、Windows 平台 CPA-Manager-Plus 管理员密码重置能力、优化安装按钮在最新状态下的轻量置灰提示，并增强应用启动时服务版本的自动检测。

### 新增与优化
- **安装备份按钮数量感知**：所有“清空安装备份”按钮（CLIProxyAPI、CPA-Manager-Plus 服务页及免安装软件列表行操作）在存在备份时动态展示备份数量（如 `清空安装备份 (3)`）；当无备份文件时自动省略括号与数量，清晰直观。
- **Windows 管理员 Key 重置**：Windows 平台卡片新增【重置密码】功能，采用与 macOS 相同的安全逻辑：重置前先正常安全停止运行中的服务，执行程序内部命令生成新管理员 Key，使用 Windows 当前用户 DPAPI 加密保存副本并自动回填至界面，完成后自动恢复服务运行。
- **安装 / 更新按钮智能置灰**：免安装软件与安装向导软件在所选版本与本地已安装版本一致（无新版本）时，按钮以灰色弱化呈现，既避免用户误以为有可用更新，又保留手动点击重装或覆盖的能力。
- **应用启动自动检测新版**：当距离上次检查超过 12 小时时，启动应用自动发起后台非阻塞最新版本检查并记录时间，避免反复频繁请求。
- **视觉与图标优化**：更新高清晰度应用图标资产，多端视觉保持一致。

### 构建与发布
- 持续提供 Windows（AMD64 / ARM64）与 macOS（Apple Silicon arm64 / Intel x86_64）四架构自动化构建发布与 SHA-256 校验清单。
- 全量自动化测试（Windows 221 项、macOS 前端 170+ 项）严格通过。

### 下载
| 平台 | 下载文件 |
| --- | --- |
| Windows AMD64 | [ZIP](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.17/CPA-Unified-Manager-v1.5.17-windows-amd64.zip) |
| Windows ARM64 | [ZIP](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.17/CPA-Unified-Manager-v1.5.17-windows-arm64.zip) |
| macOS Apple Silicon（arm64） | [DMG](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.17/CPA-Mac-Manager-v1.5.17-darwin-arm64.dmg) · [ZIP](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.17/CPA-Mac-Manager-v1.5.17-darwin-arm64.zip) |
| macOS Intel（x86_64） | [DMG](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.17/CPA-Mac-Manager-v1.5.17-darwin-x86_64.dmg) · [ZIP](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.17/CPA-Mac-Manager-v1.5.17-darwin-x86_64.zip) |

校验文件：[`SHA256SUMS.txt`](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.17/SHA256SUMS.txt)
