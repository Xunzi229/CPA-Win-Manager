"""Bundled software directory; browsing never downloads or installs anything."""

GROUPS = {
    "Windows / 系统增强": [
        ("Windows Terminal", "Windows 终端", "microsoft/terminal"),
        ("Rufus", "制作启动 U 盘", "pbatard/rufus"),
        ("Files", "现代 Windows 文件管理器", "files-community/Files"),
        ("Flow Launcher", "Windows 快速启动器", "Flow-Launcher/Flow.Launcher"),
        ("Wox", "Windows 启动器", "Wox-launcher/Wox"),
        ("EarTrumpet", "Windows 音量管理增强", "File-New-Project/EarTrumpet"),
        ("QuickLook", "按空格预览文件", "QL-Win/QuickLook"),
        ("Twinkle Tray", "多显示器亮度控制", "xanderfrangos/twinkle-tray"),
        ("AutoHotkey", "Windows 自动化脚本", "AutoHotkey/AutoHotkey"),
        ("Ditto", "剪贴板历史管理", "sabrogden/Ditto"),
        ("ExplorerPatcher", "Windows 任务栏和资源管理器定制", "valinet/ExplorerPatcher"),
        ("Open-Shell", "Windows 开始菜单增强", "Open-Shell/Open-Shell-Menu"),
        ("TranslucentTB", "透明任务栏", "TranslucentTB/TranslucentTB"),
        ("Mica For Everyone", "Windows 窗口视觉效果", "MicaForEveryone/MicaForEveryone"),
        ("ScreenToGif", "屏幕录制 GIF", "NickeManarin/ScreenToGif"),
        ("PowerToys", "Windows 系统效率工具集", "microsoft/PowerToys"),
        ("ShareX", "截图、录屏和分享", "ShareX/ShareX"),
        ("OBS Studio", "录屏和直播", "obsproject/obs-studio"),
        ("Notepad++", "文本和代码编辑器", "notepad-plus-plus/notepad-plus-plus"),
        ("WinSCP", "SFTP、FTP 和文件传输", "winscp/winscp"),
        ("SumatraPDF", "轻量 PDF 阅读器", "sumatrapdfreader/sumatrapdf"),
    ],
    "文件管理 / 搜索": [
        ("EverythingToolbar", "Everything 集成任务栏", "srwi/EverythingToolbar"),
        ("fzf", "文件和文本模糊搜索", "junegunn/fzf"),
        ("fd", "find 替代工具", "sharkdp/fd"),
        ("ripgrep", "快速文本搜索，grep 替代工具", "BurntSushi/ripgrep"),
        ("Duf", "磁盘空间查看", "muesli/duf"),
        ("Dust", "磁盘占用分析，du 替代工具", "bootandy/dust"),
        ("ncdu", "终端磁盘占用分析", "rofl0r/ncdu"),
        ("WinDirStat", "Windows 磁盘分析", "windirstat/windirstat"),
        ("Czkawka", "查找重复文件和大文件", "qarmin/czkawka"),
        ("7-Zip", "压缩和解压", "ip7z/7zip"),
    ],
    "Git / 开发效率": [
        ("GitUI", "终端 Git 客户端", "gitui-org/gitui"),
        ("Lazygit", "Git 终端界面", "jesseduffield/lazygit"),
        ("Delta", "Git diff 美化", "dandavison/delta"),
        ("GitHub CLI", "命令行管理 GitHub", "cli/cli"),
        ("Gitea", "自建 Git 服务", "go-gitea/gitea"),
        ("Forgejo", "Gitea 社区分支；源托管于 Codeberg", "https://codeberg.org/forgejo/forgejo"),
        ("Onefetch", "Git 仓库信息展示", "o2sh/onefetch"),
        ("Pre-commit", "Git 提交前自动检查", "pre-commit/pre-commit"),
        ("Commitizen", "规范 Git 提交", "commitizen-tools/commitizen"),
    ],
    "终端工具": [
        ("Tabby", "SSH 和终端客户端", "Eugeny/tabby"),
        ("Alacritty", "GPU 加速终端", "alacritty/alacritty"),
        ("WezTerm", "高性能终端", "wezterm/wezterm"),
        ("Ghostty", "GPU 终端", "ghostty-org/ghostty"),
        ("Starship", "跨 Shell 提示符", "starship/starship"),
        ("Oh My Posh", "PowerShell 和 Bash 提示符", "JanDeDobbeleer/oh-my-posh"),
        ("zoxide", "智能目录跳转", "ajeetdsouza/zoxide"),
        ("eza", "ls 替代工具", "eza-community/eza"),
        ("bat", "带语法高亮的 cat 替代工具", "sharkdp/bat"),
        ("btop", "系统监控", "aristocratos/btop"),
        ("bottom", "Rust 系统监控", "ClementTsang/bottom"),
        ("procs", "ps 替代工具", "dalance/procs"),
        ("hyperfine", "命令性能测试", "sharkdp/hyperfine"),
        ("tldr", "简化命令手册", "tldr-pages/tldr"),
        ("atuin", "Shell 历史搜索", "atuinsh/atuin"),
    ],
    "API / 数据库开发": [
        ("Bruno", "API 调试，Postman 替代工具", "usebruno/bruno"),
        ("Hoppscotch", "Web API 调试", "hoppscotch/hoppscotch"),
        ("Yaak", "API 客户端", "mountain-loop/yaak"),
        ("HTTPie", "命令行 HTTP 客户端", "httpie/cli"),
        ("Insomnia", "API 客户端", "Kong/insomnia"),
        ("DBeaver", "数据库图形客户端", "dbeaver/dbeaver"),
        ("Beekeeper Studio", "现代数据库客户端", "beekeeper-studio/beekeeper-studio"),
        ("CloudBeaver", "Web 数据库管理", "dbeaver/cloudbeaver"),
        ("Adminer", "单文件数据库管理", "vrana/adminer"),
        ("pgAdmin", "PostgreSQL 图形客户端", "pgadmin-org/pgadmin4"),
    ],
    "Docker / 容器": [
        ("Lazydocker", "Docker 终端界面", "jesseduffield/lazydocker"),
        ("Dive", "分析 Docker 镜像层", "wagoodman/dive"),
        ("ctop", "Docker 容器监控", "bcicen/ctop"),
        ("Dockge", "Docker Compose 管理", "louislam/dockge"),
        ("Portainer", "Docker Web 管理界面", "portainer/portainer"),
        ("Podman", "容器管理，Docker 替代工具", "containers/podman"),
        ("Distrobox", "容器化 Linux 开发环境", "89luca89/distrobox"),
        ("K9s", "Kubernetes 终端界面", "derailed/k9s"),
        ("Lens", "Kubernetes 图形界面", "lensapp/lens"),
        ("OpenTofu", "基础设施即代码，Terraform 替代工具", "opentofu/opentofu"),
    ],
    "服务器监控": [
        ("Uptime Kuma", "服务存活监控", "louislam/uptime-kuma"),
        ("Beszel", "轻量服务器监控", "henrygd/beszel"),
        ("Netdata", "实时性能监控", "netdata/netdata"),
        ("Prometheus", "指标监控", "prometheus/prometheus"),
        ("Grafana", "指标可视化", "grafana/grafana"),
        ("VictoriaMetrics", "高性能时序数据库", "VictoriaMetrics/VictoriaMetrics"),
        ("Glances", "系统监控", "nicolargo/glances"),
        ("Dozzle", "Docker 日志 Web 查看", "amir20/dozzle"),
        ("Scrutiny", "硬盘 SMART 监控", "AnalogJ/scrutiny"),
    ],
    "自托管 / 私有服务": [
        ("Immich", "自建照片管理服务", "immich-app/immich"),
        ("Nextcloud", "私有云盘", "nextcloud/server"),
        ("Seafile", "高性能私有网盘", "haiwen/seafile"),
        ("Paperless-ngx", "文件和 PDF 归档", "paperless-ngx/paperless-ngx"),
        ("Vaultwarden", "Bitwarden 轻量服务端", "dani-garcia/vaultwarden"),
        ("Linkwarden", "收藏夹管理", "linkwarden/linkwarden"),
        ("Wallabag", "稍后阅读", "wallabag/wallabag"),
        ("Memos", "私有笔记", "usememos/memos"),
        ("FreshRSS", "RSS 阅读器", "FreshRSS/FreshRSS"),
        ("Miniflux", "极简 RSS 阅读器", "miniflux/v2"),
        ("Homepage", "自建服务导航页", "gethomepage/homepage"),
        ("Homarr", "家庭实验室仪表盘", "homarr-labs/homarr"),
        ("Mealie", "菜谱管理", "mealie-recipes/mealie"),
        ("Home Assistant", "智能家居", "home-assistant/core"),
    ],
    "AI 工具": [
        ("Open WebUI", "本地 AI Web 界面", "open-webui/open-webui"),
        ("Ollama", "本地运行大模型", "ollama/ollama"),
        ("llama.cpp", "CPU 和 GPU 本地大模型推理", "ggml-org/llama.cpp"),
        ("AnythingLLM", "本地知识库和 AI", "Mintplex-Labs/anything-llm"),
        ("Dify", "AI 工作流和 Agent 平台", "langgenius/dify"),
        ("Flowise", "可视化 LLM 工作流", "FlowiseAI/Flowise"),
        ("Langfuse", "LLM 调用监控", "langfuse/langfuse"),
        ("Continue", "IDE AI 编程助手", "continuedev/continue"),
        ("Aider", "命令行 AI 编程", "Aider-AI/aider"),
        ("Open Interpreter", "AI 控制本机", "OpenInterpreter/open-interpreter"),
    ],
    "密码 / 安全": [
        ("KeePassXC", "本地密码管理器", "keepassxreboot/keepassxc"),
        ("Bitwarden", "密码管理客户端", "bitwarden/clients"),
        ("Vaultwarden", "Bitwarden 轻量服务端", "dani-garcia/vaultwarden"),
        ("Cryptomator", "云盘文件加密", "cryptomator/cryptomator"),
        ("VeraCrypt", "磁盘加密", "veracrypt/VeraCrypt"),
        ("Age", "文件加密", "FiloSottile/age"),
    ],
}


def catalog_entries():
    result, by_address = [], {}
    for category, rows in GROUPS.items():
        for name, description, repository in rows:
            address = repository if not repository or repository.startswith("https://") else "https://github.com/" + repository
            identity = address.casefold() if address else name.casefold()
            if identity in by_address:
                by_address[identity]["categories"].append(category)
                continue
            record = {"id": str(len(result)), "name": name, "description": description,
                      "repository": address, "categories": [category]}
            by_address[identity] = record
            result.append(record)
    return result


def filter_entries(entries, category="全部", query=""):
    query = query.strip().casefold()
    return [entry for entry in entries
            if (category == "全部" or category in entry["categories"])
            and query in " ".join((entry["name"], entry["description"], entry["repository"], *entry["categories"])).casefold()]
