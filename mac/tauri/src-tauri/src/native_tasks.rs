use std::path::PathBuf;
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use serde_json::{json, Value};
use tauri::{AppHandle, Manager};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

use crate::state::{save_config, ActiveJob, AppState, SavedConfig};

fn worker_path(app: &AppHandle) -> Result<PathBuf, String> {
    let bundled = app.path().resource_dir().map_err(|error| error.to_string())?.join("cpa-native-worker");
    if bundled.is_file() {
        return Ok(bundled);
    }
    let development = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("resources/cpa-native-worker");
    if cfg!(debug_assertions) && development.is_file() {
        return Ok(development);
    }
    Err("缺少原生业务组件，请重新安装完整的 Mac 应用包。".into())
}

pub fn request(app: &AppHandle, cfg: &SavedConfig, scope: &str, action: &str, body: Value) -> Value {
    let executable = std::env::current_exe().unwrap_or_default();
    let source = if cfg!(debug_assertions) {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().unwrap().parent().unwrap().to_path_buf()
    } else {
        executable.parent().unwrap().parent().unwrap().parent().unwrap().to_path_buf()
    };
    let _ = app;
    json!({"config": cfg, "scope": scope, "action": action, "body": body, "source_root": source})
}

fn apply_event(cfg: &mut SavedConfig, event: &Value) -> Result<(), String> {
    match event["type"].as_str().unwrap_or("") {
        "record" => {
            let records = if event["mode"] == "portable" { &mut cfg.portable } else { &mut cfg.packages };
            if let Some(record) = records.iter_mut().find(|record| record.id == event["id"] && record.repository == event["repository"]) {
                let mut value = serde_json::to_value(&*record).map_err(|error| error.to_string())?;
                for field in ["local", "latest", "asset", "update", "selected_tag", "selected_asset", "installed_id", "installed_name", "installed_version", "installed_dir", "installed_auto_disabled"] {
                    if let Some(update) = event["value"].get(field) {
                        value[field] = update.clone();
                    }
                }
                *record = serde_json::from_value(value).map_err(|error| error.to_string())?;
            }
        }
        "project" => {
            let key = event["key"].as_str().ok_or("业务组件返回了无效项目。")?;
            let directory = if key == "cli" { &cfg.cli_directory } else { &cfg.plus_directory };
            if crate::state::expand_tilde(directory).to_string_lossy() == event["directory"].as_str().unwrap_or("") {
                cfg.project_status.insert(key.into(), event["value"].clone());
            }
        }
        "download" => {
            cfg.downloaded_files.retain(|entry| entry["path"] != event["path"]);
            cfg.downloaded_files.push(json!({"path": event["path"], "sha256": event["sha256"]}));
        }
        "done" => {
            if let Some(removed) = event["result"]["removed_paths"].as_array() {
                cfg.downloaded_files.retain(|entry| !removed.contains(&entry["path"]));
            }
        }
        _ => {}
    }
    Ok(())
}

async fn run(app: &AppHandle, request: Value, job_id: Option<&str>) -> Result<Value, String> {
    let mut child = tokio::process::Command::new(worker_path(app)?)
        .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped())
        .spawn().map_err(|error| format!("启动原生业务组件失败：{error}"))?;
    let mut input = child.stdin.take().ok_or("无法连接业务组件。")?;
    input.write_all(format!("{request}\n").as_bytes()).await.map_err(|error| error.to_string())?;
    let stderr = child.stderr.take().ok_or("无法读取组件诊断。")?;
    let diagnostics = tokio::spawn(async move {
        let mut lines = BufReader::new(stderr).lines();
        let mut output = String::new();
        while let Ok(Some(line)) = lines.next_line().await {
            if output.len() < 8192 { output.push_str(&line); output.push('\n'); }
        }
        output
    });
    let mut lines = BufReader::new(child.stdout.take().ok_or("无法读取组件输出。")?).lines();
    let mut result = None;
    let mut cancel_sent = false;
    loop {
        tokio::select! {
            line = lines.next_line() => {
                let Some(line) = line.map_err(|error| error.to_string())? else { break };
                let event: Value = serde_json::from_str(&line).map_err(|error| format!("业务组件输出无效：{error}"))?;
                let state = app.state::<AppState>();
                if matches!(event["type"].as_str(), Some("record" | "project" | "download" | "done")) {
                    let mut cfg = state.config.lock().map_err(|error| error.to_string())?;
                    apply_event(&mut cfg, &event)?;
                    save_config(&cfg);
                }
                if event["type"] == "progress" {
                    if let Some(id) = job_id {
                        let mut jobs = state.job.lock().map_err(|error| error.to_string())?;
                        if let Some(job) = jobs.as_mut().filter(|job| job.id == id) {
                            if let Some(progress) = event["progress"].as_f64() { job.progress = progress.clamp(0.0, 100.0) as u32; }
                            if let Some(line) = event["line"].as_str() { job.lines.push(line.into()); }
                        }
                    }
                }
                if event["type"] == "done" {
                    result = Some(if let Some(error) = event["error"].as_str() { Err(error.into()) } else { Ok(event["result"].clone()) });
                }
            }
            _ = tokio::time::sleep(std::time::Duration::from_millis(100)), if !cancel_sent && job_id.is_some() => {
                let cancelled = {
                    let state = app.state::<AppState>();
                    let jobs = state.job.lock().map_err(|error| error.to_string())?;
                    jobs.as_ref().is_some_and(|job| Some(job.id.as_str()) == job_id && job.cancelled.load(Ordering::SeqCst))
                };
                if cancelled {
                    input.write_all(b"cancel\n").await.map_err(|error| error.to_string())?;
                    cancel_sent = true;
                }
            }
        }
    }
    drop(input);
    let status = child.wait().await.map_err(|error| error.to_string())?;
    let diagnostic = diagnostics.await.unwrap_or_default();
    if !status.success() { return Err(format!("业务组件异常退出：{status} {diagnostic}")); }
    result.unwrap_or_else(|| Err(format!("业务组件未返回操作结果。{diagnostic}")))
}

pub async fn call(app: &AppHandle, request: Value) -> Result<Value, String> {
    run(app, request, None).await
}

pub fn start(app: AppHandle, request: Value) -> Result<Value, String> {
    worker_path(&app)?;
    let identity = chrono::Utc::now().timestamp_nanos_opt().unwrap_or(0).to_string();
    {
        let state = app.state::<AppState>();
        let mut jobs = state.job.lock().map_err(|error| error.to_string())?;
        if jobs.as_ref().is_some_and(|job| !job.done) { return Err("已有任务正在执行，请等待完成或取消。".into()); }
        *jobs = Some(ActiveJob { id: identity.clone(), done: false, error: String::new(), progress: 0,
            lines: vec!["任务已开始。".into()], cancelled: Arc::new(AtomicBool::new(false)) });
    }
    let response = json!({"job_id": identity});
    tauri::async_runtime::spawn(async move {
        let result = run(&app, request, Some(&identity)).await;
        let state = app.state::<AppState>();
        if let Ok(mut jobs) = state.job.lock() {
            if let Some(job) = jobs.as_mut().filter(|job| job.id == identity) {
                job.done = true;
                if let Err(error) = result { job.error = error.clone(); job.lines.push(error); }
                else { job.progress = 100; job.lines.push("任务完成。".into()); }
            }
        };
    });
    Ok(response)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn download_events_track_exact_files_and_cleanup_removes_only_reported_paths() {
        let mut cfg = SavedConfig::default();
        apply_event(&mut cfg, &json!({"type":"download", "path":"/tmp/test.dmg", "sha256":"abc"})).unwrap();
        apply_event(&mut cfg, &json!({"type":"download", "path":"/tmp/other.dmg", "sha256":"def"})).unwrap();
        apply_event(&mut cfg, &json!({"type":"done", "result":{"removed_paths":["/tmp/test.dmg"]}})).unwrap();
        assert_eq!(cfg.downloaded_files.len(), 1);
        assert_eq!(cfg.downloaded_files[0]["path"], "/tmp/other.dmg");
    }

    #[test]
    fn project_events_do_not_overwrite_changed_directory() {
        let mut cfg = SavedConfig::default();
        apply_event(&mut cfg, &json!({"type":"project", "key":"cli", "directory":"/different", "value":{"latest":"1.0.0"}})).unwrap();
        assert!(cfg.project_status.is_empty());
    }

    #[test]
    fn release_preferences_default_and_persist_without_overwriting_latest() {
        let mut cfg = SavedConfig::default();
        let record: crate::state::SoftwareRecord = serde_json::from_value(json!({
            "id":"test", "name":"Test", "repository":"example/test", "directory":"/tmp/tool", "preserve":"",
            "latest":"v3", "asset":"auto.dmg", "local":"v1", "update":true
        })).unwrap();
        assert!(record.selected_tag.is_empty());
        assert!(record.selected_asset.is_empty());
        cfg.packages.push(record);
        apply_event(&mut cfg, &json!({"type":"record", "mode":"package", "id":"test", "repository":"example/test",
            "value":{"selected_tag":"v2", "selected_asset":"manual.zip", "asset":"manual.zip"}})).unwrap();
        let restored: SavedConfig = serde_json::from_str(&serde_json::to_string(&cfg).unwrap()).unwrap();
        assert_eq!(restored.packages[0].selected_tag, "v2");
        assert_eq!(restored.packages[0].selected_asset, "manual.zip");
        assert_eq!(restored.packages[0].latest, "v3");
        apply_event(&mut cfg, &json!({"type":"record", "mode":"package", "id":"test", "repository":"different/test",
            "value":{"selected_tag":"v4", "selected_asset":"wrong.zip"}})).unwrap();
        assert_eq!(cfg.packages[0].selected_tag, "v2");
    }
}
