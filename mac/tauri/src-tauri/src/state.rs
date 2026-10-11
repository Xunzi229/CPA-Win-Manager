use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Instant;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProjectInfo {
    pub key: String,
    pub title: String,
    pub repo: String,
    pub binary: String,
    pub config: String,
    pub directory: String,
    pub latest: String,
    pub asset: String,
    pub local: String,
    pub update: bool,
    pub installed: bool,
    pub service: String,
    pub is_running: bool,
    pub endpoint: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SoftwareRecord {
    pub id: String,
    pub name: String,
    pub repository: String,
    pub directory: String,
    pub preserve: String,
    pub latest: String,
    pub asset: String,
    pub local: String,
    pub update: bool,
    #[serde(default)]
    pub installed_id: String,
    #[serde(default)]
    pub installed_name: String,
    #[serde(default)]
    pub installed_version: String,
    #[serde(default)]
    pub installed_dir: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CatalogItem {
    pub mode: String,
    pub name: String,
    pub description: String,
    pub repository: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobSnapshot {
    pub id: String,
    pub done: bool,
    pub error: String,
    pub progress: u32,
    pub lines: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FullState {
    pub version: String,
    pub proxy_enabled: bool,
    pub proxy: String,
    pub proxy_label: String,
    pub download_directory: String,
    pub download_workers: u32,
    pub prerelease: bool,
    pub projects: HashMap<String, ProjectInfo>,
    pub portable: Vec<SoftwareRecord>,
    pub packages: Vec<SoftwareRecord>,
    pub apps_directory: String,
    pub catalog: Vec<CatalogItem>,
    pub categories: Vec<String>,
    pub manager_last_check: f64,
    pub job: Option<JobSnapshot>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SavedConfig {
    pub proxy_enabled: bool,
    pub proxy: String,
    pub download_directory: String,
    pub download_workers: u32,
    pub prerelease: bool,
    pub cli_directory: String,
    pub plus_directory: String,
    pub portable: Vec<SoftwareRecord>,
    pub packages: Vec<SoftwareRecord>,
    #[serde(default)]
    pub manager_last_check: f64,
    #[serde(default)]
    pub manager_release: Option<serde_json::Value>,
}

impl Default for SavedConfig {
    fn default() -> Self {
        let home = dirs::home_dir().unwrap_or_else(|| PathBuf::from("/Users/Shared"));
        let apps = home.join("Applications");
        let downloads = home.join("Downloads");
        Self {
            proxy_enabled: false,
            proxy: "http://127.0.0.1:7890".to_string(),
            download_directory: downloads.to_string_lossy().to_string(),
            download_workers: 4,
            prerelease: false,
            cli_directory: apps.join("CLIProxyAPI").to_string_lossy().to_string(),
            plus_directory: apps.join("CPA-Manager-Plus").to_string_lossy().to_string(),
            portable: Vec::new(),
            packages: Vec::new(),
            manager_last_check: 0.0,
            manager_release: None,
        }
    }
}

pub struct ActiveJob {
    pub id: String,
    pub done: bool,
    pub error: String,
    pub progress: u32,
    pub lines: Vec<String>,
    pub cancelled: Arc<AtomicBool>,
}

pub struct AppState {
    pub config: Mutex<SavedConfig>,
    pub job: Mutex<Option<ActiveJob>>,
    pub cache: Mutex<HashMap<String, (Instant, serde_json::Value)>>,
}

impl Default for AppState {
    fn default() -> Self {
        let config = load_config();
        Self {
            config: Mutex::new(config),
            job: Mutex::new(None),
            cache: Mutex::new(HashMap::new()),
        }
    }
}

pub fn config_path() -> PathBuf {
    let base = dirs::data_dir()
        .or_else(dirs::home_dir)
        .unwrap_or_else(|| PathBuf::from("/tmp"));
    base.join("CPA-Unified-Manager").join("tauri_state.json")
}

pub fn load_config() -> SavedConfig {
    let path = config_path();
    if let Ok(content) = std::fs::read_to_string(&path) {
        if let Ok(cfg) = serde_json::from_str(&content) {
            return cfg;
        }
    }
    let default_cfg = SavedConfig::default();
    save_config(&default_cfg);
    default_cfg
}

pub fn save_config(cfg: &SavedConfig) {
    let path = config_path();
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Ok(json) = serde_json::to_string_pretty(cfg) {
        let _ = std::fs::write(path, json);
    }
}

pub fn expand_tilde(path: &str) -> PathBuf {
    if let Some(stripped) = path.strip_prefix("~/") {
        if let Some(home) = dirs::home_dir() {
            return home.join(stripped);
        }
    }
    PathBuf::from(path)
}