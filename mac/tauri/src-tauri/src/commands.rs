fn is_brew_available() -> bool {
    if std::path::Path::new("/opt/homebrew/bin/brew").is_file()
        || std::path::Path::new("/usr/local/bin/brew").is_file() {
        return true;
    }
    std::process::Command::new("which")
        .arg("brew")
        .output()
        .map(|out| out.status.success() && !out.stdout.is_empty())
        .unwrap_or(false)
}

use std::collections::HashMap;
use std::path::PathBuf;
use std::process::Command;
use std::sync::atomic::Ordering;
use tauri::State;
use serde_json::json;

use crate::installed_apps;
use crate::github_search;
use crate::native_tasks;
use crate::state::{
    default_visible_tabs, expand_tilde, normalize_visible_tabs, save_config, AppState, CatalogItem, FullState, JobSnapshot,
    ProjectInfo, SoftwareRecord,
};

#[tauri::command]
pub async fn get_state(app: tauri::AppHandle, state: State<'_, AppState>) -> Result<FullState, String> {
    let apps = installed_apps::scan();
    let mut cfg = {
        let mut lock = state.config.lock().map_err(|e| e.to_string())?;
        if installed_apps::refresh_records(&mut lock.packages, &apps) {
            save_config(&lock);
        }
        lock.clone()
    };

    let statuses = native_tasks::call(&app, native_tasks::request(&app, &cfg, "system", "status", json!({}))).await?;
    for record in cfg.portable.iter_mut().chain(cfg.packages.iter_mut()) {
        if let Some(value) = statuses["software"].get(&record.id) {
            record.local = value["local"].as_str().unwrap_or("").into();
            record.update = value["update"].as_bool().unwrap_or(false);
        }
    }
    let mut projects = HashMap::new();

    // 1. CLIProxyAPI
    {
        let brew_installed = is_brew_available();
        let prefix = if std::path::Path::new("/opt/homebrew/bin/brew").exists() {
            "/opt/homebrew"
        } else {
            "/usr/local"
        };
        let binary_path = std::path::Path::new(prefix).join("bin/cliproxyapi");
        let installed = brew_installed && binary_path.is_file();
        let (is_running, service_str) = if !brew_installed {
            (false, "未检测到 Homebrew".to_string())
        } else if installed {
            (false, "已安装".to_string())
        } else {
            (false, "未安装".to_string())
        };
        projects.insert(
            "cli".to_string(),
            ProjectInfo {
                key: "cli".to_string(),
                title: "CLIProxyAPI".to_string(),
                repo: "https://github.com/router-for-me/CLIProxyAPI".to_string(),
                binary: "cliproxyapi".to_string(),
                config: "config.yaml".to_string(),
                directory: String::new(),
                latest: "".to_string(),
                asset: "".to_string(),
                local: "".to_string(),
                update: false,
                installed,
                service: service_str,
                is_running,
                endpoint: "http://127.0.0.1:8317/management.html".to_string(),
                admin_key: String::new(),
                admin_key_note: String::new(),
                backup_count: 0,
                brew_missing: !brew_installed,
                port_info: None,
                last_check: 0.0,
            },
        );
    }

    // 2. CPA-Manager-Plus
    {
        let root = expand_tilde(&cfg.plus_directory);
        let binary_path = root.join("cpa-manager-plus");
        let installed = binary_path.is_file();
        let (is_running, service_str) = if installed {
            (false, "正在读取状态".to_string())
        } else {
            (false, "未安装".to_string())
        };
        projects.insert(
            "plus".to_string(),
            ProjectInfo {
                key: "plus".to_string(),
                title: "CPA-Manager-Plus".to_string(),
                repo: "https://github.com/seakee/CPA-Manager-Plus".to_string(),
                binary: "cpa-manager-plus".to_string(),
                config: "config.json".to_string(),
                directory: cfg.plus_directory.clone(),
                latest: "".to_string(),
                asset: "".to_string(),
                local: "".to_string(),
                update: false,
                installed,
                service: service_str,
                is_running,
                endpoint: "http://127.0.0.1:18317/management.html".to_string(),
                admin_key: String::new(),
                admin_key_note: String::new(),
                backup_count: 0,
                brew_missing: false,
                port_info: None,
                last_check: 0.0,
            },
        );
    }

    for (key, project) in projects.iter_mut() {
        if let Some(status) = statuses.get(key) {
            let mut value = serde_json::to_value(&*project).map_err(|error| error.to_string())?;
            if let Some(fields) = status.as_object() {
                for (field, update) in fields { value[field] = update.clone(); }
            }
            *project = serde_json::from_value(value).map_err(|error| error.to_string())?;
        }
    }

    let job_snap = {
        let job_lock = state.job.lock().map_err(|e| e.to_string())?;
        job_lock.as_ref().map(|j| JobSnapshot {
            id: j.id.clone(),
            done: j.done,
            error: j.error.clone(),
            progress: j.progress,
            lines: j.lines.clone(),
        })
    };

    let catalog = vec![
        CatalogItem {
            mode: "免安装".to_string(),
            name: "ripgrep".to_string(),
            description: "高速文本正则搜索工具 (rg)".to_string(),
            repository: "BurntSushi/ripgrep".to_string(),
        },
        CatalogItem {
            mode: "免安装".to_string(),
            name: "fd".to_string(),
            description: "快速文件查找工具".to_string(),
            repository: "sharkdp/fd".to_string(),
        },
        CatalogItem {
            mode: "免安装".to_string(),
            name: "jq".to_string(),
            description: "轻量级命令行 JSON 处理器".to_string(),
            repository: "jqlang/jq".to_string(),
        },
        CatalogItem {
            mode: "安装包".to_string(),
            name: "WezTerm".to_string(),
            description: "GPU 加速跨平台终端模拟器".to_string(),
            repository: "wez/wezterm".to_string(),
        },
        CatalogItem {
            mode: "安装包".to_string(),
            name: "Alacritty".to_string(),
            description: "高性能 GPU 渲染极简终端".to_string(),
            repository: "alacritty/alacritty".to_string(),
        },
    ];

    let home = dirs::home_dir().unwrap_or_else(|| PathBuf::from("/Users/Shared"));
    let apps_dir = home.join("Applications").to_string_lossy().to_string();

    Ok(FullState {
        version: app.package_info().version.to_string(),
        proxy_enabled: cfg.proxy_enabled,
        proxy: cfg.proxy.clone(),
        proxy_label: if cfg.proxy_enabled { "代理：已启用".to_string() } else { "代理：直连".to_string() },
        download_directory: cfg.download_directory.clone(),
        download_workers: cfg.download_workers,
        prerelease: cfg.prerelease,
        visible_tabs: match normalize_visible_tabs(&cfg.visible_tabs) {
            tabs if tabs.is_empty() => default_visible_tabs(),
            tabs => tabs,
        },
        projects,
        portable: cfg.portable.clone(),
        packages: cfg.packages.clone(),
        apps_directory: apps_dir,
        catalog,
        categories: vec![
            "全部".to_string(),
            "macOS / 系统增强".to_string(),
            "文件管理 / 搜索".to_string(),
            "Git / 开发".to_string(),
            "终端工具".to_string(),
            "API / 数据库开发".to_string(),
            "Docker / 容器".to_string(),
            "AI 工具".to_string(),
            "密码 / 安全".to_string(),
        ],
        manager_last_check: cfg.manager_last_check,
        job: job_snap,
    })
}

#[tauri::command(rename_all = "snake_case")]
pub async fn save_settings(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    proxy_enabled: Option<bool>,
    proxy: Option<String>,
    download_workers: Option<u32>,
    prerelease: Option<bool>,
    download_directory: Option<String>,
    visible_tabs: Option<Vec<String>>,
) -> Result<FullState, String> {
    {
        let mut cfg = state.config.lock().map_err(|e| e.to_string())?;
        if let Some(tabs) = visible_tabs {
            let tabs = normalize_visible_tabs(&tabs);
            if tabs.is_empty() { return Err("至少保留一个功能页面。".to_string()); }
            cfg.visible_tabs = tabs;
        }
        if let Some(pe) = proxy_enabled { cfg.proxy_enabled = pe; }
        if let Some(p) = proxy { cfg.proxy = p.trim().to_string(); }
        if let Some(dw) = download_workers { cfg.download_workers = dw.clamp(1, 16); }
        if let Some(pr) = prerelease { cfg.prerelease = pr; }
        if let Some(dd) = download_directory {
            if !dd.trim().is_empty() {
                cfg.download_directory = dd.trim().to_string();
            }
        }
        save_config(&cfg);
    }
    get_state(app, state).await
}
#[tauri::command]
pub async fn choose_directory(app: tauri::AppHandle) -> Result<serde_json::Value, String> {
    use tauri_plugin_dialog::DialogExt;
    let (sender, receiver) = tokio::sync::oneshot::channel();
    app.dialog().file().set_title("选择目录").pick_folder(move |folder| {
        let _ = sender.send(folder);
    });
    let folder = receiver.await.map_err(|error| error.to_string())?;
    Ok(json!({"path": folder.map(|path| path.to_string()).unwrap_or_default()}))
}

#[tauri::command]
pub async fn open_path(path: String) -> Result<serde_json::Value, String> {
    let trimmed = path.trim();
    if trimmed.is_empty() { return Err("路径不能为空。".into()); }
    if trimmed.starts_with("http://") || trimmed.starts_with("https://") {
        #[cfg(target_os = "macos")]
        {
            let status = Command::new("open").arg(trimmed).status().map_err(|error| format!("打开系统默认浏览器失败：{error}"))?;
            if !status.success() { return Err("调用系统默认浏览器失败。".into()); }
        }
        #[cfg(not(target_os = "macos"))]
        {
            let _ = Command::new("explorer").arg(trimmed).spawn();
        }
        return Ok(json!({ "ok": true }));
    }
    let resolved = expand_tilde(trimmed);
    if !resolved.is_absolute() { return Err("请使用绝对路径。".into()); }
    if !resolved.exists() {
        std::fs::create_dir_all(&resolved).map_err(|error| format!("创建目录失败：{error}"))?;
    }
    #[cfg(target_os = "macos")]
    {
        let output = Command::new("open").arg(&resolved).output().map_err(|error| error.to_string())?;
        if !output.status.success() { return Err(String::from_utf8_lossy(&output.stderr).into()); }
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = Command::new("explorer").arg(&resolved).spawn();
    }
    Ok(json!({ "ok": true }))
}

#[tauri::command]
pub async fn project_action(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    key: String,
    action: String,
    body: serde_json::Value,
) -> Result<serde_json::Value, String> {
    if !matches!(key.as_str(), "cli" | "plus") { return Err("项目不存在。".into()); }
    if action == "directory" {
        if key == "cli" { return Ok(json!({"ok": true})); }
        let directory = body["directory"].as_str().filter(|value| !value.trim().is_empty()).ok_or("请选择目录。")?;
        let mut cfg = state.config.lock().map_err(|error| error.to_string())?;
        cfg.plus_directory = directory.trim().into();
        cfg.project_status.remove(&key);
        save_config(&cfg);
        return Ok(json!({"ok": true}));
    }
    let cfg = state.config.lock().map_err(|error| error.to_string())?.clone();
    let mut request = native_tasks::request(&app, &cfg, "project", &action, body);
    request["key"] = json!(key);
    if matches!(action.as_str(), "check" | "install" | "start" | "stop" | "restart" | "update") {
        return native_tasks::start(app, request);
    }
    native_tasks::call(&app, request).await
}

#[tauri::command]
pub async fn open_project_page(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    key: String,
) -> Result<serde_json::Value, String> {
    if !matches!(key.as_str(), "cli" | "plus") { return Err("项目不存在。".into()); }
    let cfg = state.config.lock().map_err(|error| error.to_string())?.clone();
    let mut request = native_tasks::request(&app, &cfg, "project", "management_url", json!({}));
    request["key"] = json!(key);
    let result = native_tasks::call(&app, request).await?;
    let url: tauri::Url = result["url"].as_str().ok_or("服务未返回后台地址。")?
        .parse().map_err(|_| "后台地址无效。")?;
    if url.scheme() != "http" || url.path() != "/management.html"
        || !url.username().is_empty() || url.password().is_some()
        || url.query().is_some() || url.fragment().is_some() {
        return Err("后台地址无效。".into());
    }
    let status = std::process::Command::new("open")
        .arg(url.as_str())
        .status()
        .map_err(|error| format!("打开系统默认浏览器失败：{error}"))?;
    if !status.success() {
        return Err("调用系统默认浏览器失败。".into());
    }
    Ok(json!({"ok": true}))
}

async fn write_clipboard(program: &str, text: &str) -> Result<(), String> {
    use std::process::Stdio;
    use tokio::io::AsyncWriteExt;
    if text.is_empty() || text.len() > 4096 { return Err("待复制的 Key 为空或长度无效。".into()); }
    let mut child = tokio::process::Command::new(program)
        .stdin(Stdio::piped()).stdout(Stdio::null()).stderr(Stdio::piped())
        .spawn().map_err(|_| "无法启动 macOS 剪贴板工具。".to_string())?;
    let mut input = child.stdin.take().ok_or("无法写入剪贴板。")?;
    input.write_all(text.as_bytes()).await.map_err(|_| "写入剪贴板失败。".to_string())?;
    drop(input);
    let output = child.wait_with_output().await.map_err(|_| "剪贴板工具异常退出。".to_string())?;
    if !output.status.success() { return Err("复制失败，请检查 macOS 剪贴板是否可用。".into()); }
    Ok(())
}

#[tauri::command]
pub async fn copy_text(text: String) -> Result<serde_json::Value, String> {
    write_clipboard("/usr/bin/pbcopy", &text).await?;
    Ok(json!({"ok": true}))
}

fn rand_u128() -> u128 {
    use std::time::SystemTime;
    SystemTime::now().duration_since(SystemTime::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0)
}

fn normalize_repository(value: &str) -> Result<String, String> {
    let cleaned = value.trim().trim_end_matches('/').strip_suffix(".git").unwrap_or(value.trim().trim_end_matches('/'));
    let path = cleaned.strip_prefix("https://github.com/").or_else(|| cleaned.strip_prefix("http://github.com/")).unwrap_or(cleaned);
    let parts: Vec<_> = path.split('/').collect();
    if parts.len() < 2 || parts[0].is_empty() || parts[1].is_empty()
        || !parts[..2].iter().all(|part| part.chars().all(|character| character.is_ascii_alphanumeric() || matches!(character, '-' | '_' | '.')))
        || matches!(parts[0], "." | "..") || matches!(parts[1], "." | "..") {
        return Err("请填写有效的 GitHub 仓库地址。".into());
    }
    Ok(format!("https://github.com/{}/{}", parts[0], parts[1]))
}

#[tauri::command]
pub async fn software_action(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    mode: String,
    action: String,
    body: serde_json::Value,
) -> Result<serde_json::Value, String> {
    if !matches!(mode.as_str(), "portable" | "package") { return Err("软件类型无效。".into()); }
    let is_portable = mode == "portable";
    if matches!(action.as_str(), "release_options" | "release_assets" | "select_release") {
        if action == "select_release" {
            let job = state.job.lock().map_err(|error| error.to_string())?;
            if job.as_ref().is_some_and(|job| !job.done) { return Err("请等待当前任务结束后再修改版本或附件。".into()); }
        }
        let cfg = state.config.lock().map_err(|error| error.to_string())?.clone();
        let mut request = native_tasks::request(&app, &cfg, "software", &action, body);
        request["mode"] = json!(mode);
        return native_tasks::call(&app, request).await;
    }
    if matches!(action.as_str(), "check" | "install" | "upgrade_all" | "clean_downloads" | "clear_backups" | "uninstall") {
        let cfg = state.config.lock().map_err(|error| error.to_string())?.clone();
        let mut request = native_tasks::request(&app, &cfg, "software", &action, body);
        request["mode"] = json!(mode);
        if matches!(action.as_str(), "check" | "install" | "upgrade_all") {
            return native_tasks::start(app, request);
        }
        {
            let job = state.job.lock().map_err(|error| error.to_string())?;
            if job.as_ref().is_some_and(|job| !job.done) { return Err("请等待当前任务结束后再执行此操作。".into()); }
        }
        return native_tasks::call(&app, request).await;
    }
    let mut cfg = state.config.lock().map_err(|error| error.to_string())?;
    match action.as_str() {
        "add" => {
            let mut name = body["name"].as_str().unwrap_or("").trim().to_string();
            let repo = normalize_repository(body["repository"].as_str().unwrap_or(""))?;
            let directory = body["directory"].as_str().unwrap_or("").trim().to_string();
            if name.is_empty() || name == "releases" {
                if let Some(project) = repo.rsplit('/').next() {
                    name = project.to_string();
                }
            }
            if name.is_empty() { return Err("请填写名称。".into()); }
            if is_portable && directory.is_empty() { return Err("请选择独立的解压目录。".into()); }
            let records = if is_portable { &cfg.portable } else { &cfg.packages };
            if records.iter().any(|record| normalize_repository(&record.repository).ok().is_some_and(|value| value.eq_ignore_ascii_case(&repo))) {
                return Err("该仓库已添加，请直接操作已有记录。".into());
            }
            let mut record = SoftwareRecord {
                id: format!("{:x}", rand_u128()), name, repository: repo, directory,
                preserve: body["preserve"].as_str().unwrap_or("").trim().into(),
                latest: String::new(), asset: String::new(), local: String::new(), update: false,
                selected_tag: String::new(), selected_asset: String::new(),
                installed_id: String::new(), installed_name: String::new(), installed_version: String::new(),
                installed_dir: String::new(), installed_auto_disabled: false,
                pinned: false,
            };
            if is_portable { cfg.portable.push(record); }
            else {
                installed_apps::refresh_records(std::slice::from_mut(&mut record), &installed_apps::scan());
                cfg.packages.push(record);
            }
        }
        "remove" | "edit" => {
            let id = body["id"].as_str().ok_or("请选择软件记录。")?;
            let records = if is_portable { &mut cfg.portable } else { &mut cfg.packages };
            let index = records.iter().position(|record| record.id == id).ok_or("软件记录不存在。")?;
            if action == "remove" { records.remove(index); }
            else {
                let maybe_repo = match body["repository"].as_str() {
                    Some(repo) => {
                        let normalized = normalize_repository(repo)?;
                        if records.iter().any(|r| r.id != id && normalize_repository(&r.repository).ok().is_some_and(|value| value.eq_ignore_ascii_case(&normalized))) {
                            return Err("该仓库已添加，请直接操作已有记录。".into());
                        }
                        Some(normalized)
                    }
                    None => None,
                };
                let preserve = body["preserve"].as_str().map(|val| val.trim().to_string());
                let record = &mut records[index];
                if let Some(repository) = maybe_repo {
                    if repository != record.repository {
                        record.latest.clear(); record.asset.clear(); record.update = false;
                        record.selected_tag.clear(); record.selected_asset.clear();
                        if let Some(project) = repository.rsplit("/").next() {
                            if !project.is_empty() {
                                record.name = project.to_string();
                            }
                        }
                        if !is_portable && !record.installed_auto_disabled {
                            record.installed_id.clear();
                            record.installed_name.clear();
                            record.installed_version.clear();
                            record.installed_dir.clear();
                            record.local.clear();
                        }
                    }
                    record.repository = repository;
                }
                if let Some(p) = preserve { record.preserve = p; }
                if !is_portable {
                    installed_apps::refresh_records(std::slice::from_mut(record), &installed_apps::scan());
                }
            }
        }
        "bind_installed" => {
            let id = body["id"].as_str().ok_or("请选择软件记录。")?;
            let directory = body["app_dir"].as_str().filter(|value| !value.trim().is_empty()).ok_or("请选择已安装软件。")?;
            let record = cfg.packages.iter_mut().find(|record| record.id == id).ok_or("软件记录不存在。")?;
            let app = installed_apps::read_app(&expand_tilde(directory)).ok_or("所选应用已不存在，请重新扫描。")?;
            installed_apps::associate(record, &app);
        }
        "toggle_pin" | "toggle-pin" => {
            let id = body["id"].as_str().ok_or("请选择软件记录。")?;
            let records = if is_portable { &mut cfg.portable } else { &mut cfg.packages };
            let record = records.iter_mut().find(|record| record.id == id).ok_or("软件记录不存在。")?;
            record.pinned = !record.pinned;
            let pinned = record.pinned;
            save_config(&cfg);
            return Ok(json!({"ok": true, "pinned": pinned}));
        }
        "unbind_installed" => {
            let id = body["id"].as_str().ok_or("请选择软件记录。")?;
            let record = cfg.packages.iter_mut().find(|record| record.id == id).ok_or("软件记录不存在。")?;
            installed_apps::unbind(record);
        }
        _ => return Err(format!("不支持的软件操作：{action}")),
    }
    save_config(&cfg);
    Ok(json!({"ok": true}))
}

#[tauri::command]
pub async fn get_job(state: State<'_, AppState>, job_id: String) -> Result<JobSnapshot, String> {
    let job_lock = state.job.lock().map_err(|e| e.to_string())?;
    if let Some(ref j) = *job_lock {
        if j.id == job_id {
            return Ok(JobSnapshot {
                id: j.id.clone(),
                done: j.done,
                error: j.error.clone(),
                progress: j.progress,
                lines: j.lines.clone(),
            });
        }
    }
    Err("任务不存在或已被新的任务替换。".into())
}

#[tauri::command]
pub async fn cancel_job(state: State<'_, AppState>) -> Result<serde_json::Value, String> {
    let mut job_lock = state.job.lock().map_err(|e| e.to_string())?;
    if let Some(ref mut j) = *job_lock {
        j.cancelled.store(true, Ordering::SeqCst);
        if !j.done { j.lines.push("正在取消任务，请等待当前文件操作安全结束。".to_string()); }
    }
    Ok(json!({ "ok": true }))
}

#[tauri::command]
pub async fn macos_doctor(app: tauri::AppHandle, state: State<'_, AppState>) -> Result<serde_json::Value, String> {
    let cfg = state.config.lock().map_err(|error| error.to_string())?.clone();
    native_tasks::call(&app, native_tasks::request(&app, &cfg, "system", "doctor", json!({}))).await
}


#[tauri::command]
pub async fn scan_installed_apps() -> Result<serde_json::Value, String> {
    Ok(json!({ "apps": installed_apps::scan() }))
}

#[tauri::command(rename_all = "snake_case")]
pub async fn get_catalog(
    state: State<'_, AppState>,
    category: Option<String>,
    query: Option<String>,
    search_github: Option<bool>,
) -> Result<serde_json::Value, String> {
    let cat = category.unwrap_or_else(|| "全部".to_string());
    let q = query.unwrap_or_default().trim().to_lowercase();
    let items = vec![
        json!({ "mode": "安装包", "category": "macOS / 系统增强", "name": "Raycast", "description": "macOS 极速聚焦启动器与工作流", "repository": "https://github.com/raycast/script-commands" }),
        json!({ "mode": "安装包", "category": "macOS / 系统增强", "name": "Stats", "description": "macOS 菜单栏系统资源监控", "repository": "https://github.com/exelban/stats" }),
        json!({ "mode": "安装包", "category": "macOS / 系统增强", "name": "Maccy", "description": "轻量剪贴板历史管理", "repository": "https://github.com/p0deje/Maccy" }),
        json!({ "mode": "安装包", "category": "macOS / 系统增强", "name": "AltTab", "description": "macOS 窗口切换器 (Windows Alt-Tab 体验)", "repository": "https://github.com/lwouis/alt-tab-macos" }),
        json!({ "mode": "安装包", "category": "macOS / 系统增强", "name": "LocalSend", "description": "跨平台局域网免配对传输工具", "repository": "https://github.com/localsend/localsend" }),
        json!({ "mode": "免安装", "category": "文件管理 / 搜索", "name": "ripgrep", "description": "高速文本正则搜索工具 (rg)", "repository": "https://github.com/BurntSushi/ripgrep" }),
        json!({ "mode": "免安装", "category": "文件管理 / 搜索", "name": "fd", "description": "快速高效的 find 替代工具", "repository": "https://github.com/sharkdp/fd" }),
        json!({ "mode": "免安装", "category": "文件管理 / 搜索", "name": "fzf", "description": "通用命令行模糊查找器", "repository": "https://github.com/junegunn/fzf" }),
        json!({ "mode": "免安装", "category": "Git / 开发", "name": "lazygit", "description": "终端全键盘 Git 界面", "repository": "https://github.com/jesseduffield/lazygit" }),
        json!({ "mode": "免安装", "category": "Git / 开发", "name": "gitui", "description": "极速 Rust 终端 Git 客户端", "repository": "https://github.com/gitui-org/gitui" }),
        json!({ "mode": "免安装", "category": "Git / 开发", "name": "gh", "description": "GitHub 官方命令行管理工具", "repository": "https://github.com/cli/cli" }),
        json!({ "mode": "安装包", "category": "终端工具", "name": "WezTerm", "description": "GPU 加速多标签分屏高性能终端", "repository": "https://github.com/wezterm/wezterm" }),
        json!({ "mode": "安装包", "category": "终端工具", "name": "Alacritty", "description": "极简高性能 GPU 渲染终端", "repository": "https://github.com/alacritty/alacritty" }),
        json!({ "mode": "安装包", "category": "终端工具", "name": "Ghostty", "description": "现代极速 GPU 渲染终端", "repository": "https://github.com/ghostty-org/ghostty" }),
        json!({ "mode": "免安装", "category": "终端工具", "name": "bat", "description": "带语法高亮与 Git 集成的 cat 替代品", "repository": "https://github.com/sharkdp/bat" }),
        json!({ "mode": "免安装", "category": "终端工具", "name": "eza", "description": "现代带颜色图标的 ls 替代工具", "repository": "https://github.com/eza-community/eza" }),
        json!({ "mode": "安装包", "category": "API / 数据库开发", "name": "Bruno", "description": "开源轻量 API 调试工具 (Postman 替代品)", "repository": "https://github.com/usebruno/bruno" }),
        json!({ "mode": "安装包", "category": "API / 数据库开发", "name": "DBeaver", "description": "万能数据库管理与 SQL 查询客户端", "repository": "https://github.com/dbeaver/dbeaver" }),
        json!({ "mode": "免安装", "category": "Docker / 容器", "name": "lazydocker", "description": "终端一键管理 Docker 容器与镜像", "repository": "https://github.com/jesseduffield/lazydocker" }),
        json!({ "mode": "免安装", "category": "Docker / 容器", "name": "k9s", "description": "全键盘极速 Kubernetes 集群控制终端", "repository": "https://github.com/derailed/k9s" }),
        json!({ "mode": "安装包", "category": "AI 工具", "name": "Ollama", "description": "本地运行大型语言模型 (Llama/Qwen 等)", "repository": "https://github.com/ollama/ollama" }),
        json!({ "mode": "安装包", "category": "密码 / 安全", "name": "KeePassXC", "description": "离线高安全密码管理器", "repository": "https://github.com/keepassxreboot/keepassxc" }),
    ];
    let mut filtered: Vec<_> = items.into_iter().filter(|it| {
        let item_cat = it["category"].as_str().unwrap_or("");
        let item_name = it["name"].as_str().unwrap_or("");
        let item_desc = it["description"].as_str().unwrap_or("");
        let item_repo = it["repository"].as_str().unwrap_or("");
        if cat != "全部" && item_cat != cat { return false; }
        if !q.is_empty() {
            let combined = format!("{} {} {}", item_name, item_desc, item_repo).to_lowercase();
            if !combined.contains(&q) { return false; }
        }
        true
    }).collect();
    let mut total = filtered.len() as u64;
    if search_github.unwrap_or(false) && !q.is_empty() {
        let proxy = {
            let cfg = state.config.lock().map_err(|error| error.to_string())?;
            if cfg.proxy_enabled { cfg.proxy.clone() } else { String::new() }
        };
        let results = github_search::search(&q, &proxy).await?;
        filtered = results.items;
        total = results.total;
    }
    Ok(json!({
        "items": filtered,
        "total": total,
        "categories": vec![
            "全部", "macOS / 系统增强", "文件管理 / 搜索", "Git / 开发",
            "终端工具", "API / 数据库开发", "Docker / 容器", "AI 工具", "密码 / 安全"
        ]
    }))
}

type VersionKey = ((u64, u64, u64), bool, Vec<(u8, u64, String)>);

fn version_key(version: &str) -> Option<VersionKey> {
    let version = version.trim();
    let version = version.strip_prefix('v').unwrap_or(version);
    let version = version.split('+').next()?;
    let (core, prerelease) = match version.split_once('-') {
        Some((core, prerelease)) => (core, Some(prerelease)),
        None => (version, None),
    };
    let parts: Vec<u64> = core.split('.').map(|part| part.parse().ok()).collect::<Option<_>>()?;
    if parts.len() != 3 || prerelease == Some("") {
        return None;
    }
    let identifiers = prerelease.map(|value| value.split('.').map(|part| match part.parse::<u64>() {
        Ok(number) => (0, number, String::new()),
        Err(_) => (1, 0, part.to_string()),
    }).collect()).unwrap_or_default();
    Some(((parts[0], parts[1], parts[2]), prerelease.is_none(), identifiers))
}

fn has_update(local: &str, latest: &str) -> bool {
    matches!((version_key(local), version_key(latest)), (Some(local), Some(latest)) if latest > local)
}

#[tauri::command]
pub async fn check_manager_update(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    force: Option<bool>,
) -> Result<serde_json::Value, String> {
    let now = chrono::Utc::now().timestamp() as f64;
    let cfg = state.config.lock().map_err(|error| error.to_string())?.clone();
    if !force.unwrap_or(false) && now - cfg.manager_last_check < 18000.0 {
        if let Some(mut release) = cfg.manager_release.clone() {
            let current = app.package_info().version.to_string();
            let latest = release.get("latest").and_then(|value| value.as_str()).unwrap_or("").to_string();
            release["current"] = json!(current);
            release["has_update"] = json!(has_update(&current, &latest));
            release["cached"] = json!(true);
            return Ok(release);
        }
    }
    let release = native_tasks::call(&app, native_tasks::request(&app, &cfg, "system", "manager_update", json!({"force": force.unwrap_or(false)}))).await?;
    {
        let mut cfg = state.config.lock().map_err(|error| error.to_string())?;
        cfg.manager_last_check = now;
        cfg.manager_release = Some(release.clone());
        save_config(&cfg);
    }
    Ok(release)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn clipboard_writer_reports_invalid_input_and_process_failure() {
        assert!(write_clipboard("/bin/cat", "").await.is_err());
        assert!(write_clipboard("/bin/cat", &"a".repeat(4097)).await.is_err());
        assert!(write_clipboard("/bin/cat", "fixture-key").await.is_ok());
        assert!(write_clipboard("/bin/false", "fixture-key").await.is_err());
    }

    #[test]
    fn visible_tabs_keep_known_keys_in_fixed_order() {
        let tabs = vec!["package".to_string(), "unknown".to_string(), "cli".to_string(), "cli".to_string()];
        assert_eq!(normalize_visible_tabs(&tabs), vec!["cli".to_string(), "package".to_string()]);
        assert!(normalize_visible_tabs(&["unknown".to_string()]).is_empty());
        let saved: crate::state::SavedConfig = serde_json::from_value(json!({
            "proxy_enabled": false, "proxy": "", "download_directory": "", "download_workers": 4,
            "prerelease": false, "cli_directory": "", "plus_directory": "", "portable": [], "packages": []
        })).unwrap();
        assert_eq!(saved.visible_tabs, default_visible_tabs());
    }

    #[test]
    fn manager_update_compares_cached_latest_with_local_version() {
        assert!(has_update("1.5.16", "1.5.17"));
        assert!(has_update("1.5.17-beta.1", "v1.5.17"));
        assert!(has_update("1.5.17-beta.2", "1.5.17-beta.10"));
        assert!(!has_update("1.5.17", "1.5.17"));
        assert!(!has_update("1.5.17", "v1.5.16"));
        assert!(!has_update("1.5.17", ""));
    }

    #[test]
    fn repository_urls_are_normalized_for_duplicate_detection() {
        assert_eq!(normalize_repository("2dust/v2rayN").unwrap(), "https://github.com/2dust/v2rayN");
        assert_eq!(normalize_repository("https://github.com/2dust/v2rayN/releases").unwrap(), "https://github.com/2dust/v2rayN");
        for value in ["", "../repo", "https://evil.example/owner/repo", "owner"] {
            assert!(normalize_repository(value).is_err());
        }
    }
}
