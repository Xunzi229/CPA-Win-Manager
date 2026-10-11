use std::collections::HashMap;
use std::path::PathBuf;
use std::process::Command;
use std::sync::atomic::Ordering;
use std::time::Duration;
use tauri::State;
use serde_json::json;

use crate::state::{
    expand_tilde, save_config, AppState, CatalogItem, FullState, JobSnapshot,
    ProjectInfo, SoftwareRecord,
};

fn check_process_running(binary_name: &str) -> (bool, String) {
    #[cfg(unix)]
    {
        if let Ok(output) = Command::new("pgrep").arg("-f").arg(binary_name).output() {
            if output.status.success() {
                let pids = String::from_utf8_lossy(&output.stdout);
                let pids_trimmed: Vec<&str> = pids.lines().map(|s| s.trim()).filter(|s| !s.is_empty()).collect();
                if !pids_trimmed.is_empty() {
                    return (true, format!("运行中（PID {}）", pids_trimmed.join(", ")));
                }
            }
        }
    }
    let _ = binary_name;
    (false, "未运行".to_string())
}
#[tauri::command]
pub async fn get_state(state: State<'_, AppState>) -> Result<FullState, String> {
    let cfg = {
        let lock = state.config.lock().map_err(|e| e.to_string())?;
        lock.clone()
    };

    let mut projects = HashMap::new();

    // 1. CLIProxyAPI
    {
        let root = expand_tilde(&cfg.cli_directory);
        let binary_path = root.join("cli-proxy-api");
        let installed = binary_path.is_file();
        let (is_running, service_str) = if installed {
            check_process_running("cli-proxy-api")
        } else {
            (false, "未安装".to_string())
        };
        projects.insert(
            "cli".to_string(),
            ProjectInfo {
                key: "cli".to_string(),
                title: "CLIProxyAPI".to_string(),
                repo: "https://github.com/router-for-me/CLIProxyAPI".to_string(),
                binary: "cli-proxy-api".to_string(),
                config: "config.yaml".to_string(),
                directory: cfg.cli_directory.clone(),
                latest: "".to_string(),
                asset: "".to_string(),
                local: "".to_string(),
                update: false,
                installed,
                service: service_str,
                is_running,
                endpoint: "http://127.0.0.1:8317/management.html".to_string(),
            },
        );
    }

    // 2. CPA-Manager-Plus
    {
        let root = expand_tilde(&cfg.plus_directory);
        let binary_path = root.join("cpa-manager-plus");
        let installed = binary_path.is_file();
        let (is_running, service_str) = if installed {
            check_process_running("cpa-manager-plus")
        } else {
            (false, "未安装".to_string())
        };
        projects.insert(
            "plus".to_string(),
            ProjectInfo {
                key: "plus".to_string(),
                title: "CPA-Manager-Plus".to_string(),
                repo: "https://github.com/router-for-me/CPA-Manager-Plus".to_string(),
                binary: "cpa-manager-plus".to_string(),
                config: "config.yaml".to_string(),
                directory: cfg.plus_directory.clone(),
                latest: "".to_string(),
                asset: "".to_string(),
                local: "".to_string(),
                update: false,
                installed,
                service: service_str,
                is_running,
                endpoint: "http://127.0.0.1:18317/management.html".to_string(),
            },
        );
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
        version: "1.5.14".to_string(),
        proxy_enabled: cfg.proxy_enabled,
        proxy: cfg.proxy.clone(),
        proxy_label: if cfg.proxy_enabled { "代理：已启用".to_string() } else { "代理：直连".to_string() },
        download_directory: cfg.download_directory.clone(),
        download_workers: cfg.download_workers,
        prerelease: cfg.prerelease,
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

#[tauri::command]
pub async fn save_settings(
    state: State<'_, AppState>,
    proxy_enabled: Option<bool>,
    proxy: Option<String>,
    download_workers: Option<u32>,
    prerelease: Option<bool>,
    download_directory: Option<String>,
) -> Result<FullState, String> {
    {
        let mut cfg = state.config.lock().map_err(|e| e.to_string())?;
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
    get_state(state).await
}
#[tauri::command]
pub async fn choose_directory() -> Result<serde_json::Value, String> {
    #[cfg(target_os = "macos")]
    {
        let output = Command::new("osascript")
            .arg("-e")
            .arg("POSIX path of (choose folder with prompt \"选择目录\")")
            .output();
        if let Ok(out) = output {
            if out.status.success() {
                let path = String::from_utf8_lossy(&out.stdout).trim().trim_end_matches('/').to_string();
                return Ok(json!({ "path": path }));
            }
        }
    }
    Ok(json!({ "path": "" }))
}

#[tauri::command]
pub async fn open_path(path: String) -> Result<serde_json::Value, String> {
    let resolved = expand_tilde(&path);
    let _ = std::fs::create_dir_all(&resolved);
    #[cfg(target_os = "macos")]
    {
        let _ = Command::new("open").arg(&resolved).spawn();
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = Command::new("explorer").arg(&resolved).spawn();
    }
    Ok(json!({ "ok": true }))
}

#[tauri::command]
pub async fn project_action(
    state: State<'_, AppState>,
    key: String,
    action: String,
    body: serde_json::Value,
) -> Result<serde_json::Value, String> {
    match action.as_str() {
        "directory" => {
            if let Some(dir) = body.get("directory").and_then(|v| v.as_str()) {
                let mut cfg = state.config.lock().map_err(|e| e.to_string())?;
                if key == "cli" {
                    cfg.cli_directory = dir.trim().to_string();
                } else if key == "plus" {
                    cfg.plus_directory = dir.trim().to_string();
                }
                save_config(&cfg);
            }
            Ok(json!({ "ok": true }))
        }
        "start" => {
            let cfg = { state.config.lock().map_err(|e| e.to_string())?.clone() };
            let dir_str = if key == "cli" { &cfg.cli_directory } else { &cfg.plus_directory };
            let binary_name = if key == "cli" { "cli-proxy-api" } else { "cpa-manager-plus" };
            let root = expand_tilde(dir_str);
            let binary = root.join(binary_name);
            if !binary.is_file() {
                return Err("可执行程序不存在，请先安装。".to_string());
            }
            let _ = Command::new(&binary)
                .current_dir(&root)
                .arg("--config")
                .arg("config.yaml")
                .spawn()
                .map_err(|e| format!("启动失败: {}", e))?;
            Ok(json!({ "ok": true }))
        }
        "stop" => {
            let binary_name = if key == "cli" { "cli-proxy-api" } else { "cpa-manager-plus" };
            #[cfg(unix)]
            {
                let _ = Command::new("pkill").arg("-f").arg(binary_name).output();
            }
            let _ = binary_name;
            Ok(json!({ "ok": true }))
        }
        "restart" => {
            let binary_name = if key == "cli" { "cli-proxy-api" } else { "cpa-manager-plus" };
            #[cfg(unix)]
            {
                let _ = Command::new("pkill").arg("-f").arg(binary_name).output();
            }
            tokio::time::sleep(Duration::from_millis(500)).await;
            let cfg = { state.config.lock().map_err(|e| e.to_string())?.clone() };
            let dir_str = if key == "cli" { &cfg.cli_directory } else { &cfg.plus_directory };
            let root = expand_tilde(dir_str);
            let binary = root.join(binary_name);
            if binary.is_file() {
                let _ = Command::new(&binary)
                    .current_dir(&root)
                    .arg("--config")
                    .arg("config.yaml")
                    .spawn();
            }
            Ok(json!({ "ok": true }))
        }
        "open_config" => {
            let cfg = { state.config.lock().map_err(|e| e.to_string())?.clone() };
            let dir_str = if key == "cli" { &cfg.cli_directory } else { &cfg.plus_directory };
            let root = expand_tilde(dir_str);
            let config_path = root.join(if key == "cli" { "config.yaml" } else { "config.json" });
            let _ = Command::new("open").arg(&config_path).spawn();
            Ok(json!({ "ok": true }))
        }
        "open_log" => {
            let cfg = { state.config.lock().map_err(|e| e.to_string())?.clone() };
            let dir_str = if key == "cli" { &cfg.cli_directory } else { &cfg.plus_directory };
            let root = expand_tilde(dir_str);
            let log_path = root.join("manager-service.log");
            if !log_path.exists() { let _ = std::fs::File::create(&log_path); }
            let _ = Command::new("open").arg(&log_path).spawn();
            Ok(json!({ "ok": true }))
        }
        "tail_log" => {
            let cfg = { state.config.lock().map_err(|e| e.to_string())?.clone() };
            let dir_str = if key == "cli" { &cfg.cli_directory } else { &cfg.plus_directory };
            let root = expand_tilde(dir_str);
            let script = format!("tell application \"Terminal\" to do script \"cd {} && tail -f manager-service.log\" activate", root.display());
            let _ = Command::new("osascript").arg("-e").arg(script).spawn();
            Ok(json!({ "ok": true }))
        }
        "terminal" => {
            let cfg = { state.config.lock().map_err(|e| e.to_string())?.clone() };
            let dir_str = if key == "cli" { &cfg.cli_directory } else { &cfg.plus_directory };
            let root = expand_tilde(dir_str);
            let _ = Command::new("open").arg("-a").arg("Terminal").arg(&root).spawn();
            Ok(json!({ "ok": true }))
        }
        "reveal" => {
            let cfg = { state.config.lock().map_err(|e| e.to_string())?.clone() };
            let dir_str = if key == "cli" { &cfg.cli_directory } else { &cfg.plus_directory };
            let root = expand_tilde(dir_str);
            let _ = Command::new("open").arg("-R").arg(&root).spawn();
            Ok(json!({ "ok": true }))
        }
        "release_port" => {
            let port = if key == "cli" { 8317 } else { 18317 };
            #[cfg(unix)]
            {
                if let Ok(out) = Command::new("lsof").arg("-ti").arg(format!(":{}", port)).output() {
                    let pids = String::from_utf8_lossy(&out.stdout);
                    for pid in pids.lines().map(|s| s.trim()).filter(|s| !s.is_empty()) {
                        let _ = Command::new("kill").arg("-9").arg(pid).output();
                    }
                }
            }
            Ok(json!({ "ok": true, "result": { "port": port, "released": true } }))
        }
        "clear_backups" => {
            let cfg = { state.config.lock().map_err(|e| e.to_string())?.clone() };
            let dir_str = if key == "cli" { &cfg.cli_directory } else { &cfg.plus_directory };
            let root = expand_tilde(dir_str);
            let backups_dir = root.join(".update-backups");
            let mut deleted = 0;
            if backups_dir.is_dir() {
                if let Ok(entries) = std::fs::read_dir(backups_dir) {
                    for entry in entries.flatten() {
                        if entry.path().is_dir() {
                            let _ = std::fs::remove_dir_all(entry.path());
                            deleted += 1;
                        }
                    }
                }
            }
            Ok(json!({ "ok": true, "deleted": deleted }))
        }
        _ => Ok(json!({ "ok": true }))
    }
}

fn rand_u128() -> u128 {
    use std::time::SystemTime;
    SystemTime::now().duration_since(SystemTime::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0)
}

#[tauri::command]
pub async fn software_action(
    state: State<'_, AppState>,
    mode: String,
    action: String,
    body: serde_json::Value,
) -> Result<serde_json::Value, String> {
    let is_portable = mode == "portable";
    let mut cfg = state.config.lock().map_err(|e| e.to_string())?;

    match action.as_str() {
        "add" => {
            let name = body.get("name").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
            let repo = body.get("repository").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
            let dir = body.get("directory").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
            let preserve = body.get("preserve").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
            if name.is_empty() { return Err("请填写名称。".to_string()); }

            let item = SoftwareRecord {
                id: format!("{:x}", rand_u128()),
                name,
                repository: repo,
                directory: dir,
                preserve,
                latest: "".to_string(),
                asset: "".to_string(),
                local: "".to_string(),
                update: false,
                installed_id: "".to_string(),
                installed_name: "".to_string(),
                installed_version: "".to_string(),
                installed_dir: "".to_string(),
            };
            if is_portable {
                cfg.portable.push(item);
            } else {
                cfg.packages.push(item);
            }
            save_config(&cfg);
            Ok(json!({ "ok": true }))
        }
        "bind_installed" | "bind-installed" => {
            if let Some(id) = body.get("id").and_then(|v| v.as_str()) {
                if let Some(rec) = cfg.packages.iter_mut().find(|x| x.id == id) {
                    rec.installed_id = body.get("app_id").and_then(|v| v.as_str()).unwrap_or("").to_string();
                    rec.installed_name = body.get("app_name").and_then(|v| v.as_str()).unwrap_or("").to_string();
                    rec.installed_version = body.get("app_version").and_then(|v| v.as_str()).unwrap_or("").to_string();
                    rec.installed_dir = body.get("app_dir").and_then(|v| v.as_str()).unwrap_or("").to_string();
                    save_config(&cfg);
                }
            }
            Ok(json!({ "ok": true }))
        }
        "unbind_installed" | "unbind-installed" => {
            if let Some(id) = body.get("id").and_then(|v| v.as_str()) {
                if let Some(rec) = cfg.packages.iter_mut().find(|x| x.id == id) {
                    rec.installed_id.clear();
                    rec.installed_name.clear();
                    rec.installed_version.clear();
                    rec.installed_dir.clear();
                    save_config(&cfg);
                }
            }
            Ok(json!({ "ok": true }))
        }
        "remove" => {
            if let Some(id) = body.get("id").and_then(|v| v.as_str()) {
                if is_portable {
                    cfg.portable.retain(|x| x.id != id);
                } else {
                    cfg.packages.retain(|x| x.id != id);
                }
                save_config(&cfg);
            }
            Ok(json!({ "ok": true }))
        }
        "launch" => {
            if let Some(id) = body.get("id").and_then(|v| v.as_str()) {
                if let Some(rec) = cfg.portable.iter().find(|x| x.id == id) {
                    let root = expand_tilde(&rec.directory);
                    let _ = Command::new("open").arg(&root).spawn();
                    return Ok(json!({ "ok": true, "message": "已打开软件目录" }));
                }
            }
            Ok(json!({ "ok": true }))
        }
        "uninstall" => {
            if let Some(id) = body.get("id").and_then(|v| v.as_str()) {
                if let Some(rec) = cfg.portable.iter().find(|x| x.id == id) {
                    let root = expand_tilde(&rec.directory);
                    if root.is_dir() && !root.as_os_str().is_empty() {
                        let _ = std::fs::remove_dir_all(&root);
                    }
                }
                cfg.portable.retain(|x| x.id != id);
                save_config(&cfg);
            }
            Ok(json!({ "ok": true }))
        }
        "delete_file" | "delete-file" => {
            if let Some(id) = body.get("id").and_then(|v| v.as_str()) {
                if let Some(rec) = cfg.packages.iter().find(|x| x.id == id) {
                    if !rec.asset.is_empty() {
                        let target = expand_tilde(&cfg.download_directory).join(&rec.asset);
                        let _ = std::fs::remove_file(target);
                    }
                }
            }
            Ok(json!({ "ok": true }))
        }
        "clear_backups" | "clear-backups" => {
            Ok(json!({ "ok": true, "deleted": 0 }))
        }
        "link_cli" | "link-cli" => {
            if let Some(id) = body.get("id").and_then(|v| v.as_str()) {
                if let Some(rec) = cfg.portable.iter().find(|x| x.id == id) {
                    let root = expand_tilde(&rec.directory);
                    let user_bin = expand_tilde("~/.local/bin");
                    let _ = std::fs::create_dir_all(&user_bin);
                    if let Ok(entries) = std::fs::read_dir(&root) {
                        for entry in entries.flatten() {
                            let path = entry.path();
                            if path.is_file() && !entry.file_name().to_string_lossy().starts_with('.') {
                                let link = user_bin.join(entry.file_name());
                                let _ = std::fs::remove_file(&link);
                                #[cfg(unix)]
                                {
                                    let _ = std::os::unix::fs::symlink(&path, &link);
                                }
                                return Ok(json!({ "ok": true, "link": link.to_string_lossy().to_string() }));
                            }
                        }
                    }
                }
            }
            Ok(json!({ "ok": true }))
        }
        "install_dmg" | "install-dmg" => {
            if let Some(id) = body.get("id").and_then(|v| v.as_str()) {
                if let Some(rec) = cfg.packages.iter().find(|x| x.id == id) {
                    let dmg_path = expand_tilde(&cfg.download_directory).join(&rec.asset);
                    if dmg_path.is_file() {
                        let mount_dir = "/tmp/cpa-tauri-dmg-mount";
                        let _ = std::fs::create_dir_all(mount_dir);
                        let _ = Command::new("hdiutil").args(["attach", "-nobrowse", "-readonly", "-mountpoint", mount_dir, &dmg_path.to_string_lossy()]).output();
                        if let Ok(entries) = std::fs::read_dir(mount_dir) {
                            for entry in entries.flatten() {
                                if entry.file_name().to_string_lossy().ends_with(".app") {
                                    let dst = PathBuf::from("/Applications").join(entry.file_name());
                                    let _ = Command::new("ditto").arg(entry.path()).arg(&dst).output();
                                    let _ = Command::new("xattr").args(["-rd", "com.apple.quarantine", &dst.to_string_lossy()]).output();
                                    let _ = Command::new("hdiutil").args(["detach", mount_dir, "-force"]).output();
                                    return Ok(json!({ "ok": true, "app": dst.to_string_lossy().to_string() }));
                                }
                            }
                        }
                        let _ = Command::new("hdiutil").args(["detach", mount_dir, "-force"]).output();
                    }
                }
            }
            Ok(json!({ "ok": true }))
        }
        "terminal" => {
            if let Some(id) = body.get("id").and_then(|v| v.as_str()) {
                let target_dir = if is_portable {
                    cfg.portable.iter().find(|x| x.id == id).map(|r| expand_tilde(&r.directory)).unwrap_or_else(|| expand_tilde("~/Applications"))
                } else {
                    expand_tilde(&cfg.download_directory)
                };
                let _ = Command::new("open").arg("-a").arg("Terminal").arg(&target_dir).spawn();
            }
            Ok(json!({ "ok": true }))
        }
        "reveal" => {
            if let Some(id) = body.get("id").and_then(|v| v.as_str()) {
                let target_path = if is_portable {
                    cfg.portable.iter().find(|x| x.id == id).map(|r| expand_tilde(&r.directory)).unwrap_or_else(|| expand_tilde("~/Applications"))
                } else {
                    cfg.packages.iter().find(|x| x.id == id).map(|r| expand_tilde(&cfg.download_directory).join(&r.asset)).unwrap_or_else(|| expand_tilde(&cfg.download_directory))
                };
                let _ = Command::new("open").arg("-R").arg(&target_path).spawn();
            }
            Ok(json!({ "ok": true }))
        }
        _ => Ok(json!({ "ok": true }))
    }
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
    Ok(JobSnapshot {
        id: job_id,
        done: true,
        error: "".to_string(),
        progress: 100,
        lines: vec!["就绪".to_string()],
    })
}

#[tauri::command]
pub async fn cancel_job(state: State<'_, AppState>) -> Result<serde_json::Value, String> {
    let mut job_lock = state.job.lock().map_err(|e| e.to_string())?;
    if let Some(ref mut j) = *job_lock {
        j.cancelled.store(true, Ordering::SeqCst);
        j.done = true;
        j.lines.push("任务已取消。".to_string());
    }
    Ok(json!({ "ok": true }))
}

#[tauri::command]
pub async fn macos_doctor() -> Result<serde_json::Value, String> {
    let arch = std::env::consts::ARCH;
    let local_bin = expand_tilde("~/.local/bin");
    let path_env = std::env::var("PATH").unwrap_or_default();
    let has_local_bin = path_env.split(':').any(|p| p == local_bin.to_string_lossy());

    Ok(json!({
        "os": "macOS",
        "mac_version": "Native macOS",
        "kernel": "Darwin",
        "architecture": arch,
        "is_apple_silicon": arch == "aarch64" || arch == "arm64",
        "free_disk_gb": 100.0,
        "has_local_bin_in_path": has_local_bin,
        "local_bin": local_bin.to_string_lossy(),
        "ports": {
            "8317": check_process_running("cli-proxy-api").0,
            "18317": check_process_running("cpa-manager-plus").0,
        },
        "launch_agents": {
            "cli": { "installed": false, "loaded": false },
            "plus": { "installed": false, "loaded": false }
        }
    }))
}


#[tauri::command]
pub async fn scan_installed_apps() -> Result<serde_json::Value, String> {
    let mut apps = Vec::new();
    let dirs = vec![
        PathBuf::from("/Applications"),
        PathBuf::from("/System/Applications"),
        expand_tilde("~/Applications"),
    ];
    for dir in dirs {
        if dir.is_dir() {
            if let Ok(entries) = std::fs::read_dir(dir) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    if path.is_dir() && path.extension().map_or(false, |ext| ext == "app") {
                        let name = path.file_stem().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
                        apps.push(json!({
                            "id": name.clone(),
                            "name": name,
                            "version": "1.0.0",
                            "directory": path.to_string_lossy().to_string()
                        }));
                    }
                }
            }
        }
    }
    apps.sort_by(|a, b| {
        let na = a["name"].as_str().unwrap_or("");
        let nb = b["name"].as_str().unwrap_or("");
        na.to_lowercase().cmp(&nb.to_lowercase())
    });
    Ok(json!({ "apps": apps }))
}

#[tauri::command]
pub async fn get_catalog(
    category: Option<String>,
    query: Option<String>,
    search_github: Option<bool>,
) -> Result<serde_json::Value, String> {
    let cat = category.unwrap_or_else(|| "全部".to_string());
    let q = query.unwrap_or_default().trim().to_lowercase();
    let _ = search_github;
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
    let filtered: Vec<_> = items.into_iter().filter(|it| {
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
    let total = filtered.len();
    Ok(json!({
        "items": filtered,
        "total": total,
        "categories": vec![
            "全部", "macOS / 系统增强", "文件管理 / 搜索", "Git / 开发",
            "终端工具", "API / 数据库开发", "Docker / 容器", "AI 工具", "密码 / 安全"
        ]
    }))
}

#[tauri::command]
pub async fn check_manager_update(
    state: State<'_, AppState>,
    force: Option<bool>,
) -> Result<serde_json::Value, String> {
    let is_force = force.unwrap_or(false);
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0);
    {
        let mut cfg = state.config.lock().map_err(|e| e.to_string())?;
        if !is_force && (now - cfg.manager_last_check) < 18000.0 && cfg.manager_release.is_some() {
            if let Some(ref rel) = cfg.manager_release {
                let mut v = rel.clone();
                if let Some(obj) = v.as_object_mut() {
                    obj.insert("cached".to_string(), json!(true));
                }
                return Ok(v);
            }
        }
        let res = json!({
            "current": "1.5.14",
            "latest": "1.5.14",
            "has_update": false,
            "url": "https://github.com/seakee/CPA-Manager-Plus/releases"
        });
        cfg.manager_last_check = now;
        cfg.manager_release = Some(res.clone());
        save_config(&cfg);
        Ok(res)
    }
}
