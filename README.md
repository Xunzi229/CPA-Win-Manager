# CPA 统一管理器

用于在 Windows 上统一管理 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) 和 [CPA-Manager-Plus](https://github.com/seakee/CPA-Manager-Plus)。两个项目分别安装、更新和控制服务，共用代理设置。

## 功能

- 根据 Windows 系统架构下载并校验两个项目的 amd64 或 ARM64 版本；更新时保留原有配置和数据。
- 查看本地与最新版本，启动、停止或重启各自的服务。
- 自动检查更新，并在有新版本时显示提示。
- 保存项目目录和公共代理设置；支持查看与保存 CPA-Manager-Plus 登录 Key。

## 使用

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
