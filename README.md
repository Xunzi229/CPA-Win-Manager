# CPA 统一管理器

用于在 Windows 上统一管理 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) 和 [CPA-Manager-Plus](https://github.com/seakee/CPA-Manager-Plus)。两个项目分别安装、更新和控制服务，共用代理设置。

## 功能

- 根据 Windows 系统架构下载并校验两个项目的 amd64 或 ARM64 版本；更新时保留原有配置和数据。
- 查看本地与最新版本，启动、停止或重启各自的服务。
- 自动检查更新，并在有新版本时显示提示。
- 保存项目目录和公共代理设置；支持查看与保存 CPA-Manager-Plus 登录 Key。
- 显示管理器自身版本，启动后自动检查更新，也可手动检查；使用公共代理下载对应架构的发布包，校验 SHA256 后自动重启更新，不修改设置或停止两个项目的服务。

管理器自身更新入口位于窗口顶部。源码运行时，更新按钮打开发布页面，不覆盖源码。更新暂存文件和旧版备份保存在 EXE 同目录的 .manager-update-* 文件夹；替换失败时尝试恢复旧版，错误记录为该文件夹下的 update-error.log。请确保 EXE 所在目录可写。

## 使用

### 通用 GitHub 软件安装

在“通用安装”页点击“添加软件”，填写公开 GitHub 仓库或 Releases 地址和独立安装目录，点击“检查 / 获取附件”，选择与 Windows 架构对应的发布附件。切换已保存软件时会自动获取附件；连续快速切换只查询最后选中的软件，空地址不查询。

- **便携安装**：支持 ZIP 和单个 EXE。ZIP 仅有一层或多层外包装目录时自动去除包装；替换同名文件，不删除其他已有文件。安装前请关闭目标软件。
- **安装器**：支持 EXE/MSI，下载完成后可以打开软件自己的安装向导；页面填写的是下载目录，实际安装位置由向导决定，管理器不判定向导是否安装成功。
- **附件选择**：附件列表同步显示文件大小，每次手动选择要安装的包。
- **下载与中断**：根据文件大小使用最多 4 个线程分段下载，显示已下载大小、总大小和速度。点击“中断下载”保留分段，再次选择同一附件可续传（包括重启管理器后）；缓存位于管理器目录的 `.download-cache`。服务器不支持分段时退回单线程，中断后重新下载。文件标识或服务器校验标识变化时重新下载；下载完成进入安装阶段后不再提供中断按钮。
- **额外保留文件 / 目录**：默认留空。安装仅覆盖包内存在的同名文件，其他已有文件不覆盖、不删除。需要额外保护的包内同名文件可填写相对路径，以分号分隔，例如 `config.yaml;data`；已有内容不会被覆盖，缺失的默认文件仍可新增。已保存的保留路径继续生效，可自行清空。
- GitHub 附件提供 SHA256 或标准校验文件时验证下载；没有可识别校验值时在日志中说明。安装记录用于显示由本管理器安装的版本，不执行陌生软件探测版本。
- 便携安装备份保存在安装目录的 `.install-backup-*` 中，替换失败会恢复已修改文件。软件配置存于 `manager-settings.json`，移除记录不会删除安装文件。不同软件应选择不同目录。

暂不支持源码构建、RAR/7z/tar 包、私有仓库和无人值守安装；GitHub API 限流或没有正式 Release 时会显示错误。

1. 从 [Releases](https://github.com/Xunzi229/CPA-Win-Manager/releases/latest) 下载与 Windows 架构对应的 amd64 或 ARM64 压缩包，解压后运行 `CPA-Unified-Manager.exe`。
2. 在对应页面选择项目目录，点击“安装最新版”，然后启动服务。
3. 使用 CLIProxyAPI 前，在安装目录的 `config.yaml` 中配置上游账号。

首次安装默认仅监听本机：CLIProxyAPI 使用端口 8317，CPA-Manager-Plus 使用端口 18317。程序配置保存在 EXE 同目录的 `manager-settings.json`。

每次推送 `v*` 标签时，GitHub Actions 会分别构建 Windows amd64 和 ARM64 安装包并发布 Release。

## 从源码构建

需要 Windows、Python 3.10+ 和 PowerShell。在仓库根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

生成的 `CPA-Unified-Manager.exe` 位于仓库根目录。运行源码前先安装依赖：

```powershell
python -m pip install -r requirements.txt
python manager.py
```
