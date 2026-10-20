## CPA 统一管理器 v1.5.19

本版本优化 Windows 软件版本检查与安装判断，完善免安装软件录入和软件库链接交互；Windows 与 macOS 均新增功能页显示设置，macOS 改进 CLIProxyAPI 更新方式。

### Windows：版本检查与安装
- **单行检查实时刷新**：免安装软件和安装向导软件每次点击行内“检查”都会重新请求 GitHub，跳过版本缓存；免安装软件下方“检查 / 获取附件”同样实时刷新。
- **全部检查复用缓存**：批量检查允许使用有效缓存（有效期 10 分钟）；单行检查成功后回写共用缓存，后续批量检查使用更新后的版本和附件信息。请求失败时提示错误并保留上次成功数据。
- **跳过无需更新的软件**：安装向导软件点击“下载全部待更新”时，本地已安装版本大于或等于最新版本的软件会跳过；获取版本后再次核对，减少重复下载和安装。
- **完善版本比较**：新增本地版本大于等于发行版本的判断，兼容 `v` 前缀、四段版本号与预发布版本。

### Windows：录入与软件库交互
- **空输入框可直接填写**：免安装软件的 GitHub 地址、额外保留文件 / 目录为空时可直接输入，已有内容时双击编辑，完成保存后恢复锁定。
- **自动读取已有安装配置**：选择解压目录添加软件时，读取目录中的 `.github-install.json`，自动录入仓库地址、本地版本及安装包名称，并兼容已有元数据格式。
- **地址旁新增跳转箭头**：软件库的蓝色 `↗` 紧跟地址显示，悬停变色并显示手型；点击箭头使用系统默认浏览器打开项目页面，点击地址文本不再直接跳转。
- **完善构建环境检测**：构建时检测失效的 Python 虚拟环境并重建，改善本地打包兼容性。

### Windows 与 macOS：功能页设置
- 设置中可选择显示 CLIProxyAPI、CPA-Manager-Plus、免安装软件、安装向导软件页面，默认全部显示，至少保留一页。
- 隐藏的项目页跳过启动时版本检查；设置界面采用统一卡片布局。

### macOS：CLIProxyAPI 更新
- 对 Homebrew 安装的 CLIProxyAPI，更新时直接下载 GitHub 发布版并覆盖程序，减少等待 Homebrew 更新配方的时间。

### 文档与发布
- README 补充 Windows / macOS 界面截图对照，并遮盖截图中的个人信息。
- 提供 Windows AMD64 / ARM64、macOS Apple Silicon arm64 / Intel x86_64 安装包及 SHA-256 校验清单。

### 下载
| 平台 | 下载文件 |
| --- | --- |
| Windows AMD64 | [ZIP](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.19/CPA-Unified-Manager-v1.5.19-windows-amd64.zip) |
| Windows ARM64 | [ZIP](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.19/CPA-Unified-Manager-v1.5.19-windows-arm64.zip) |
| macOS Apple Silicon（arm64） | [DMG](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.19/CPA-Mac-Manager-v1.5.19-darwin-arm64.dmg) · [ZIP](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.19/CPA-Mac-Manager-v1.5.19-darwin-arm64.zip) |
| macOS Intel（x86_64） | [DMG](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.19/CPA-Mac-Manager-v1.5.19-darwin-x86_64.dmg) · [ZIP](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.19/CPA-Mac-Manager-v1.5.19-darwin-x86_64.zip) |

校验文件：[`SHA256SUMS.txt`](https://github.com/Xunzi229/CPA-Win-Manager/releases/download/v1.5.19/SHA256SUMS.txt)

完整变更：[v1.5.18...v1.5.19](https://github.com/Xunzi229/CPA-Win-Manager/compare/v1.5.18...v1.5.19)
