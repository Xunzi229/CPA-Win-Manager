## CPA 统一管理器 v1.5.18

本版本重点修复 Windows 重装系统后配置无法解密导致管理器打不开的问题，修复 macOS 升级后仍提示“发现新版”的问题，并为 macOS 软件列表增加按列排序与置顶。

### 修复
- **Windows 重装系统后无法启动**：软件设置由 Windows DPAPI 加密的 `manager-settings.dat` 改为明文 `manager-settings.json`，重装系统或更换机器后可直接沿用。旧版加密配置在原机器上自动迁移；无法解密时（如重装过系统）自动备份为 `manager-settings.dat.bak-时间戳` 并以默认配置正常启动，同时弹窗提示备份位置，不再需要手动删除配置文件。
- **macOS 升级后仍提示发现新版**：管理器更新检查缓存只用于保存远端最新版本，每次展示时都与当前本地版本重新比较，升级后不再残留“发现新版”提示，与 Windows 端逻辑保持一致。

### 新增与优化
- **macOS 软件列表按列排序**：免安装软件与安装向导软件表格支持点击列头进行自然排序。
- **macOS 软件置顶**：支持将常用软件置顶，置顶状态持久保存，排序时置顶行始终排在最前。
- **macOS 列表简化**：移除拖动排序与名称旁的更新箭头标记，列表展示更简洁。

### 构建与发布
- 持续提供 Windows（AMD64 / ARM64）与 macOS（Apple Silicon arm64 / Intel x86_64）四架构自动化构建发布与 SHA-256 校验清单。

### 下载
| 平台 | 下载文件 |
| --- | --- |
| Windows AMD64 | [ZIP](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.18/CPA-Unified-Manager-v1.5.18-windows-amd64.zip) |
| Windows ARM64 | [ZIP](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.18/CPA-Unified-Manager-v1.5.18-windows-arm64.zip) |
| macOS Apple Silicon（arm64） | [DMG](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.18/CPA-Mac-Manager-v1.5.18-darwin-arm64.dmg) · [ZIP](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.18/CPA-Mac-Manager-v1.5.18-darwin-arm64.zip) |
| macOS Intel（x86_64） | [DMG](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.18/CPA-Mac-Manager-v1.5.18-darwin-x86_64.dmg) · [ZIP](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.18/CPA-Mac-Manager-v1.5.18-darwin-x86_64.zip) |

校验文件：[`SHA256SUMS.txt`](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.18/SHA256SUMS.txt)
