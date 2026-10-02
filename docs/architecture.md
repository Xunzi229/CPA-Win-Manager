# 项目结构与开发约定

根目录只保留启动入口、构建脚本、依赖和说明。应用源码位于 `cpa_manager/`，测试位于 `tests/`。

```text
manager.py                      # 源码运行与 PyInstaller 的稳定入口
build.ps1                       # 本地和 Actions 共用的构建脚本
requirements.txt                # 运行依赖
cpa_manager/
  app.py                        # 主窗口、公共设置与页面协调
  config.py                     # 项目定义、默认配置与旧配置迁移
  core/
    paths.py                    # 运行根目录和资源目录
    version.py                  # 源码及打包版本
    runtime.py                  # 系统架构、进程、窗口与版本检测
    network.py                  # HTTP 连接及代理
    locking.py                  # Windows 文件锁
    files.py                    # Windows 文件名和相对路径校验
    download.py                 # 多线程下载、断点缓存、暂停与取消
    single_instance.py          # 单实例及已有窗口激活
  backends/
    cli.py                      # CLIProxyAPI 安装、配置和服务控制
    plus.py                     # CPA-Manager-Plus 安装、凭据和服务控制
    github.py                   # 共享的 Release 查询、附件推荐和下载校验
    portable.py                 # ZIP / 单 EXE 的便携安装与安装记录
    installer.py                # 安装器软件记录、包复用、下载与清理
    manager_update.py           # 管理器自身校验更新与替换
  ui/
    pages/
      project.py                # 两个固定项目共用的服务页面
      portable.py               # 便携软件列表
      installer.py              # 安装器软件列表
    widgets/
      table_choices.py          # 行内下拉及双击编辑
tests/                          # unittest 自动发现的回归测试
assets/                         # 程序图标
docs/                           # 开发文档
.github/workflows/release.yml    # amd64 / ARM64 构建和 Release 发布
```

## 模块职责

`core` 只提供基础能力，不导入后端或页面。`backends` 调用基础能力，负责查询、校验、安装和服务控制，不创建 Tkinter 窗口。`ui` 处理用户操作、后台任务与界面状态，调用后端执行工作。`app.py` 组装页面和公共设置，`config.py` 保存项目定义和配置迁移规则。

便携和安装器共用 `backends.github` 的仓库识别、发布查询、系统架构推荐与下载校验，安装行为分别留在各自的后端。两个固定项目和断点下载共用网络连接及文件锁，避免代理校验和锁实现重复。

## 路径与兼容

源码运行的根目录是仓库根目录；EXE 运行的根目录是 EXE 所在目录，与进程当前工作目录无关。图标等打包资源从 PyInstaller 的资源目录读取。移动源码文件不会改变 `manager-settings.json`、`.download-cache/`、安装备份和软件目录的位置。

配置键 `custom_software`、`installer_software` 及现有安装记录格式继续使用，旧的安装器配置迁移逻辑保留。页面显示名称与用户操作不因目录整理而改变。

## 开发与验证

在仓库根目录执行：

```powershell
python -m pip install -r requirements.txt
python manager.py
python -m unittest discover -q
```

构建默认仍输出到仓库根目录，也可指定独立输出目录，避免覆盖正在运行的 EXE：

```powershell
.\build.ps1
.\build.ps1 -OutputDirectory .build\preview
```

验证指定的打包程序：

```powershell
$env:CPA_TEST_FROZEN_EXE = (Resolve-Path .build\preview\CPA-Unified-Manager.exe).Path
.\.build\venv\Scripts\python.exe -m unittest discover -q
```

GitHub Actions 复用同一个构建入口，再运行测试、架构和启动校验，通过后打包并发布两个架构的 ZIP 及 SHA256 校验文件。
