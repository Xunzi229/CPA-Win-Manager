# CPA 统一管理器

Windows 软件安装与更新工具。内置管理 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) 和 [CPA-Manager-Plus](https://github.com/seakee/CPA-Manager-Plus)，也可以添加其他带 GitHub Release 的公开软件。

## 主要功能

- 安装、更新、启动和停止 CLIProxyAPI 与 CPA-Manager-Plus。
- 管理免安装软件：支持 ZIP 和单文件 EXE，自动推荐适合当前系统架构的附件。
- 管理安装向导软件：下载并打开 EXE/MSI 安装包，已下载且版本未变化时直接复用。
- 多线程、断点续传和暂停下载，并在可用时校验 SHA256。
- 查看本地版本与最新版本，单独检查或批量下载待更新软件。
- 自动保存软件配置、窗口位置和大小。
- 自动检查并更新管理器自身。

## 使用方法

1. 从 [Releases](https://github.com/Xunzi229/CPA-Win-Manager/releases/latest) 下载与 Windows 架构对应的压缩包。
2. 解压并运行 `CPA-Unified-Manager.exe`。
3. 在对应页面选择安装目录，填写 GitHub 仓库或 Releases 地址，然后获取附件并安装。

免安装软件支持 ZIP 和单文件 EXE。ZIP 会解压到所选目录，单文件 EXE 会直接保存到该目录。安装只替换安装包中存在的同名文件，不删除其他文件；如需保护包内的配置或数据，可在“额外保留文件 / 目录”中填写相对路径。

安装向导软件统一使用“设置”中的下载目录，默认是 Windows 系统下载目录。“已下载版本”表示安装包版本，不代表安装向导已成功完成。

软件列表支持双击修改名称、GitHub 地址或安装目录，也可以通过右键菜单检查更新、安装、打开目录、清理文件或移除记录。软件源地址必须唯一，多个免安装软件可以使用同一个安装根目录。

## 配置与缓存

- `manager-settings.dat`：软件设置，使用 Windows DPAPI 加密，绑定当前 Windows 用户和机器。旧版 `manager-settings.json` 会自动迁移。
- `.release-cache/`：GitHub 版本和附件缓存。
- `.download-cache/`：未完成下载的断点数据。
- `.install-backup-*`、`.update-backups/`：安装或更新备份，可在软件行中清理。

管理器会记住窗口位置、大小和最大化状态。如果显示器布局发生变化，窗口会自动调整到可见区域。

目前支持公开 GitHub 仓库中的 ZIP、EXE 和 MSI，不支持私有仓库、RAR/7z/tar、源码包安装和无人值守安装。

## 从源码运行

需要 Windows、Python 3.10+ 和 PowerShell。

```powershell
python -m pip install -r requirements.txt
python manager.py
```

运行测试：

```powershell
python -m unittest discover -q
```

本地打包：

```powershell
.\build.ps1
```

生成文件为仓库根目录下的 `CPA-Unified-Manager.exe`。推送 `v*` 标签后，GitHub Actions 会构建 Windows amd64 和 ARM64 版本并发布 Release。
