use std::path::PathBuf;
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;

use serde_json::{json, Value};
use tauri::{AppHandle, Manager};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

use crate::state::{save_config, ActiveJob, AppState, JobQueue, SavedConfig};

static NEXT_JOB_SEQ: AtomicU64 = AtomicU64::new(1);

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
                let mut current = cfg.project_status.get(key).cloned().unwrap_or_else(|| json!({}));
                if let Some(fields) = event["value"].as_object() {
                    for (field, update) in fields { current[field] = update.clone(); }
                } else {
                    current = event["value"].clone();
                }
                cfg.project_status.insert(key.into(), current);
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
                        if let Some(job) = jobs.get_mut(id).filter(|job| !job.done) {
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
                    job_id
                        .and_then(|id| jobs.get(id))
                        .is_some_and(|job| job.cancelled.load(Ordering::SeqCst))
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

pub(crate) fn enqueue_job(jobs: &mut JobQueue, identity: String, request: Value) -> Result<Option<Value>, String> {
    let is_check = request["action"].as_str() == Some("check");
    if jobs.has_unfinished() {
        if !is_check {
            return Err("已有任务正在执行，请等待完成或取消。".into());
        }
        jobs.items.push_back(ActiveJob {
            id: identity,
            done: false,
            queued: true,
            error: String::new(),
            progress: 0,
            lines: vec!["已加入检查队列，等待当前任务完成…".into()],
            cancelled: Arc::new(AtomicBool::new(false)),
            request: Some(request),
        });
        return Ok(None);
    }
    jobs.items.push_back(ActiveJob {
        id: identity,
        done: false,
        queued: false,
        error: String::new(),
        progress: 0,
        lines: vec!["任务已开始。".into()],
        cancelled: Arc::new(AtomicBool::new(false)),
        request: None,
    });
    Ok(Some(request))
}

pub(crate) fn finish_and_take_next(jobs: &mut JobQueue, identity: &str, result: Result<(), String>) -> Option<(String, Value)> {
    if let Some(job) = jobs.get_mut(identity) {
        job.done = true;
        job.queued = false;
        job.request = None;
        if let Err(error) = result {
            job.error = error.clone();
            job.lines.push(error);
        } else {
            job.progress = 100;
            job.lines.push("任务完成。".into());
        }
    }
    jobs.prune_finished();
    while let Some(pos) = jobs.items.iter().position(|job| !job.done && job.queued) {
        let job = &mut jobs.items[pos];
        if job.cancelled.load(Ordering::SeqCst) {
            job.done = true;
            job.queued = false;
            job.request = None;
            job.error = "排队检查已取消。".into();
            job.lines.push("排队检查已取消。".into());
            continue;
        }
        job.queued = false;
        job.lines.push("任务已开始。".into());
        if let Some(request) = job.request.take() {
            return Some((job.id.clone(), request));
        }
        job.done = true;
        job.error = "任务请求无效。".into();
    }
    None
}

pub fn start(app: AppHandle, request: Value) -> Result<Value, String> {
    worker_path(&app)?;
    let seq = NEXT_JOB_SEQ.fetch_add(1, Ordering::Relaxed);
    let identity = format!("{}-{seq}", chrono::Utc::now().timestamp_nanos_opt().unwrap_or(0));
    let immediate = {
        let state = app.state::<AppState>();
        let mut jobs = state.job.lock().map_err(|error| error.to_string())?;
        enqueue_job(&mut jobs, identity.clone(), request)?
    };
    let queued = immediate.is_none();
    let response = json!({"job_id": identity, "queued": queued});
    let Some(mut current_request) = immediate else { return Ok(response); };
    let mut current_id = identity;
    tauri::async_runtime::spawn(async move {
        loop {
            let outcome = run(&app, current_request, Some(&current_id)).await.map(|_| ());
            let state = app.state::<AppState>();
            let next = state.job.lock().ok().and_then(|mut jobs| finish_and_take_next(&mut jobs, &current_id, outcome));
            let Some((next_id, mut next_request)) = next else { break };
            if let Ok(cfg) = state.config.lock() {
                if let Ok(cfg_value) = serde_json::to_value(&*cfg) {
                    next_request["config"] = cfg_value;
                }
            }
            current_id = next_id;
            current_request = next_request;
        }
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
    fn project_events_merge_fields_including_last_check() {
        let mut cfg = SavedConfig::default();
        let dir = crate::state::expand_tilde(&cfg.cli_directory).to_string_lossy().to_string();
        apply_event(&mut cfg, &json!({"type":"project", "key":"cli", "directory": dir, "value":{"latest":"1.0.0", "last_check": 1000.0}})).unwrap();
        assert_eq!(cfg.project_status["cli"]["last_check"], 1000.0);
        apply_event(&mut cfg, &json!({"type":"project", "key":"cli", "directory": dir, "value":{"service":"running"}})).unwrap();
        assert_eq!(cfg.project_status["cli"]["latest"], "1.0.0");
        assert_eq!(cfg.project_status["cli"]["last_check"], 1000.0);
        assert_eq!(cfg.project_status["cli"]["service"], "running");
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

    #[test]
    fn check_requests_queue_in_fifo_order_without_rejecting_and_keep_history() {
        let mut jobs = JobQueue::default();
        let first = enqueue_job(&mut jobs, "job-1".into(), json!({"scope":"software", "action":"install", "body":{"id":"a"}})).unwrap();
        assert!(first.is_some());
        assert!(!jobs.get("job-1").unwrap().queued);

        let non_check = enqueue_job(&mut jobs, "job-block".into(), json!({"scope":"software", "action":"install", "body":{"id":"b"}}));
        assert_eq!(non_check.unwrap_err(), "已有任务正在执行，请等待完成或取消。");

        let second = enqueue_job(&mut jobs, "job-2".into(), json!({"scope":"software", "action":"check", "body":{"id":"b"}})).unwrap();
        let third = enqueue_job(&mut jobs, "job-3".into(), json!({"scope":"project", "action":"check", "key":"cli"})).unwrap();
        assert!(second.is_none());
        assert!(third.is_none());
        assert!(jobs.get("job-2").unwrap().queued);
        assert!(jobs.get("job-3").unwrap().queued);
        assert_eq!(jobs.current_snapshot().unwrap().id, "job-1");

        let (next_id, next_req) = finish_and_take_next(&mut jobs, "job-1", Ok(())).expect("job-2 should start next");
        assert_eq!(next_id, "job-2");
        assert_eq!(next_req["body"]["id"], "b");
        assert!(jobs.get("job-1").unwrap().done);
        assert!(!jobs.get("job-2").unwrap().queued);
        assert!(jobs.get("job-3").unwrap().queued);
        assert_eq!(jobs.current_snapshot().unwrap().id, "job-2");

        jobs.cancel_all();
        assert!(jobs.get("job-2").unwrap().cancelled.load(Ordering::SeqCst));
        assert!(jobs.get("job-3").unwrap().done);
        assert_eq!(jobs.get("job-3").unwrap().error, "排队检查已取消。");
        assert!(finish_and_take_next(&mut jobs, "job-2", Err("任务已取消。".into())).is_none());
        assert!(!jobs.has_unfinished());
    }
}
