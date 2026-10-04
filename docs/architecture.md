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
    models.py                   # 稳定记录 ID 和选择状态
    download.py                 # 多线程下载、断点缓存、暂停与取消
    software_tasks.py           # 按行异步检查、跨页面顺序下载队列
    single_instance.py          # 单实例及已有窗口激活
  backends/
    cli.py                      # CLIProxyAPI 安装、配置和服务控制
    plus.py                     # CPA-Manager-Plus 安装、凭据和服务控制
    github.py                   # 共享的 Release 查询、附件推荐和下载校验
    release_cache.py            # 按仓库独立持久化的版本附件缓存
    portable.py                 # ZIP / 单 EXE 的便携安装与安装记录
    installer.py                # 安装向导软件记录、包复用、下载与清理
    manager_update.py           # 管理器自身校验更新与替换
  ui/
    pages/
      project.py                # 两个固定项目共用的服务页面
      portable.py               # 免安装软件列表
      installer.py              # 安装向导软件列表
    widgets/
      table_choices.py          # 行内下拉及双击编辑
      frozen_actions.py         # 固定行操作与双表滚动、选择同步
tests/                          # unittest 自动发现的回归测试
assets/                         # 程序图标
docs/                           # 开发文档
.github/workflows/release.yml    # amd64 / ARM64 构建和 Release 发布
.github/workflows/test.yml       # 分支 push / PR 的双架构构建测试
```

## 模块职责

`core` 只提供基础能力，不导入后端或页面。`backends` 调用基础能力，负责查询、校验、安装和服务控制，不创建 Tkinter 窗口。`ui` 处理用户操作、后台任务与界面状态，调用后端执行工作。`app.py` 组装页面和公共设置，`config.py` 保存项目定义和配置迁移规则。

便携和安装器共用 `backends.github` 的仓库识别、发布查询、系统架构推荐与下载校验，安装行为分别留在各自的后端。两个固定项目和断点下载共用网络连接及文件锁，避免代理校验和锁实现重复。

## 路径与兼容

源码运行的根目录是仓库根目录；EXE 运行的根目录是 EXE 所在目录，与进程当前工作目录无关。图标等打包资源从 PyInstaller 的资源目录读取。配置使用加密的 `manager-settings.dat`，旧版 `manager-settings.json` 自动迁移。移动源码文件不会改变配置、`.download-cache/`、安装备份和软件目录的位置。

配置键 `custom_software`、`installer_software` 及现有安装记录格式继续使用，旧的安装器配置迁移逻辑保留。页面显示名称与用户操作不因目录整理而改变。

免安装软件的版本附件缓存位于运行根目录的 `.release-cache/`，按规范化仓库地址分别保存；旧配置内的缓存会自动迁移，先原子写入缓存再保存精简配置。配置内容没有变化时不重复写盘。列表以持久 ID 标识记录，切换行复用已读取的本地版本，安装完成或手动刷新时失效。旧安装器记录如果缺少有效仓库地址，则原样保留在配置中，不混入便携安装列表；补正配置中的地址后可迁移到安装器页。

任务控制区分下载与文件提交阶段。下载可暂停、继续和取消，取消保留断点；文件提交开始后等待提交或回滚结束，避免强制退出留下半套文件。关闭窗口会请求停止任务，批量安装器任务停止处理后续行，并禁止退出过程中自动启动安装向导。

免安装和安装向导页面共用 `core.software_tasks`。检查最多并行执行 4 项，下载队列只执行 1 项，按加入顺序推进；重复提交同一页同一行会被拒绝。任务持有提交时的软件、附件和目标目录快照，不随当前选中行改变。后台线程只向事件队列写入数据，页面在主线程处理结果和更新控件。检查、下载和文件写入分别保留取消控制，关闭窗口时等待任务结束。

## 开发与验证

在仓库根目录执行：

```powershell
python -m pip install -r requirements.txt
python manager.py
python -m unittest discover -q
```

构建默认仍输出到仓库根目录，也可指定独立输出目录，避免覆盖正在运行的 EXE：

交付文件统一命名为 `CPA-Unified-Manager.exe`，不添加 `-new` 等后缀；使用独立输出目录时也保持同名。

`build.ps1` 会在编译前正常关闭与输出 EXE 路径相同的管理器，最多等待 60 秒让文件操作结束；仍未退出则停止构建。编译成功后，如果构建前该程序正在运行，自动重新打开。其他路径的同名程序不会关闭，未运行时不会自动启动，构建失败也不会启动未完成的产物。

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

普通分支 push 和 pull request 运行独立 Test 工作流，在 Windows amd64 与 ARM64 上构建并运行回归和 EXE 架构校验；只有标签发布工作流负责创建 Release。

测试中的安装目录、下载目录和可写配置必须由 `TemporaryDirectory` 创建，不能使用真实软件目录、仓库目录或系统默认下载目录。页面测试显式构造软件配置，禁止通过 `default_profiles()` 自动发现本机安装；删除或覆盖前校验目标位于该测试的临时目录内。打包程序只作为只读输入复制到临时目录后验证，不直接修改原程序。
