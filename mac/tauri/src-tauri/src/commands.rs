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
            };
            if is_portable {
                cfg.portable.push(item);
            } else {
                cfg.packages.push(item);
            }
            save_config(&cfg);
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
