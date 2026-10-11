use std::collections::HashSet;
use std::path::{Path, PathBuf};

use serde::Serialize;

use crate::state::{expand_tilde, SoftwareRecord};

#[derive(Debug, Serialize)]
pub struct InstalledApp {
    pub id: String,
    pub name: String,
    pub version: String,
    pub directory: String,
    pub bundle_id: String,
    #[serde(skip)]
    aliases: Vec<String>,
}

pub fn read_app(path: &Path) -> Option<InstalledApp> {
    if !path.is_dir() || path.extension()?.to_str()?.to_lowercase() != "app" {
        return None;
    }
    let resolved = path.canonicalize().ok()?;
    let stem = path.file_stem()?.to_string_lossy().to_string();
    let metadata = plist::Value::from_file(resolved.join("Contents/Info.plist"))
        .ok()
        .and_then(|value| value.into_dictionary());
    let field = |key: &str| {
        metadata
            .as_ref()
            .and_then(|dictionary| dictionary.get(key))
            .and_then(|value| match value {
                plist::Value::String(text) => Some(text.trim().to_string()),
                plist::Value::Integer(number) => Some(number.to_string()),
                _ => None,
            })
            .unwrap_or_default()
    };
    let display_name = field("CFBundleDisplayName");
    let bundle_name = field("CFBundleName");
    let name = [&display_name, &bundle_name, &stem]
        .into_iter()
        .find(|value| !value.is_empty())?
        .clone();
    let short_version = field("CFBundleShortVersionString");
    let version = if short_version.is_empty() {
        field("CFBundleVersion")
    } else {
        short_version
    };
    let directory = resolved.to_string_lossy().to_string();
    Some(InstalledApp {
        id: directory.clone(),
        name,
        version,
        directory,
        bundle_id: field("CFBundleIdentifier"),
        aliases: vec![display_name, bundle_name, stem],
    })
}

fn scan_directory(
    directory: &Path,
    depth: usize,
    visited: &mut HashSet<PathBuf>,
    apps: &mut Vec<InstalledApp>,
) {
    let Ok(resolved) = directory.canonicalize() else {
        return;
    };
    if !visited.insert(resolved) {
        return;
    }
    if let Some(app) = read_app(directory) {
        apps.push(app);
        return;
    }
    if depth == 0
        || matches!(
            directory.extension().and_then(|value| value.to_str()),
            Some("framework" | "bundle" | "plugin")
        )
    {
        return;
    }
    let Ok(entries) = std::fs::read_dir(directory) else {
        return;
    };
    for entry in entries.flatten() {
        if !entry.file_name().to_string_lossy().starts_with('.') && entry.path().is_dir() {
            scan_directory(&entry.path(), depth - 1, visited, apps);
        }
    }
}

fn scan_roots(roots: &[PathBuf]) -> Vec<InstalledApp> {
    let mut apps = Vec::new();
    let mut visited = HashSet::new();
    for root in roots {
        scan_directory(root, 4, &mut visited, &mut apps);
    }
    apps.sort_by(|first, second| {
        first
            .name
            .to_lowercase()
            .cmp(&second.name.to_lowercase())
            .then_with(|| first.directory.cmp(&second.directory))
    });
    apps
}

pub fn scan() -> Vec<InstalledApp> {
    scan_roots(&[
        PathBuf::from("/Applications"),
        PathBuf::from("/System/Applications"),
        expand_tilde("~/Applications"),
    ])
}

fn normalized_name(value: &str) -> String {
    value
        .chars()
        .filter(|character| character.is_alphanumeric())
        .flat_map(char::to_lowercase)
        .collect()
}

fn automatic_match<'apps>(
    record: &SoftwareRecord,
    apps: &'apps [InstalledApp],
) -> Option<&'apps InstalledApp> {
    let repository = record
        .repository
        .trim_end_matches('/')
        .trim_end_matches(".git");
    let repository_name = repository.rsplit('/').next().unwrap_or_default();
    for name in [&record.name, repository_name] {
        let key = normalized_name(name);
        if key.is_empty() {
            continue;
        }
        let matches: Vec<_> = apps
            .iter()
            .filter(|app| {
                app.aliases
                    .iter()
                    .any(|alias| normalized_name(alias) == key)
            })
            .collect();
        if !matches.is_empty() {
            return if matches.len() == 1 {
                Some(matches[0])
            } else {
                None
            };
        }
    }
    None
}

pub fn associate(record: &mut SoftwareRecord, app: &InstalledApp) {
    record.installed_id.clone_from(&app.id);
    record.installed_name.clone_from(&app.name);
    record.installed_version.clone_from(&app.version);
    record.installed_dir.clone_from(&app.directory);
    record.local.clone_from(&app.version);
    record.installed_auto_disabled = false;
}

pub fn unbind(record: &mut SoftwareRecord) {
    record.installed_id.clear();
    record.installed_name.clear();
    record.installed_version.clear();
    record.installed_dir.clear();
    record.local.clear();
    record.update = false;
    record.installed_auto_disabled = true;
}

pub fn refresh_records(records: &mut [SoftwareRecord], apps: &[InstalledApp]) -> bool {
    let mut changed = false;
    for record in records {
        let previous = record.clone();
        let bound_app = if record.installed_dir.is_empty() {
            None
        } else {
            read_app(&expand_tilde(&record.installed_dir))
        };
        if let Some(app) = bound_app {
            associate(record, &app);
        } else {
            let disabled = record.installed_auto_disabled;
            unbind(record);
            record.installed_auto_disabled = disabled;
            if !disabled {
                if let Some(app) = automatic_match(record, apps) {
                    associate(record, app);
                }
            }
        }
        changed |= *record != previous;
    }
    changed
}

#[cfg(test)]
mod tests {
    use super::*;
    use plist::{Dictionary, Value};
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};

    static NEXT_FIXTURE: AtomicU64 = AtomicU64::new(0);

    struct Fixture(PathBuf);

    impl Fixture {
        fn new() -> Self {
            let unique = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let sequence = NEXT_FIXTURE.fetch_add(1, Ordering::Relaxed);
            let root = std::env::temp_dir().join(format!(
                "cpa-apps-{}-{unique}-{sequence}",
                std::process::id()
            ));
            std::fs::create_dir_all(&root).unwrap();
            Self(root)
        }

        fn app(&self, relative: &str, fields: &[(&str, &str)], binary: bool) -> PathBuf {
            let app = self.0.join(relative);
            std::fs::create_dir_all(app.join("Contents")).unwrap();
            let dictionary: Dictionary = fields
                .iter()
                .map(|(key, value)| (key.to_string(), Value::String(value.to_string())))
                .collect();
            let metadata = Value::Dictionary(dictionary);
            let file = app.join("Contents/Info.plist");
            if binary {
                metadata.to_file_binary(file).unwrap();
            } else {
                metadata.to_file_xml(file).unwrap();
            }
            app
        }

        fn scan(&self) -> Vec<InstalledApp> {
            scan_roots(std::slice::from_ref(&self.0))
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn record(name: &str, repository: &str) -> SoftwareRecord {
        serde_json::from_value(serde_json::json!({
            "id": "test", "name": name, "repository": repository, "directory": "",
            "preserve": "", "latest": "", "asset": "", "local": "", "update": false
        }))
        .unwrap()
    }

    #[test]
    fn reads_real_versions_from_xml_and_binary_plists() {
        let fixture = Fixture::new();
        for binary in [false, true] {
            let path = fixture.app(
                &format!("App-{binary}.app"),
                &[
                    ("CFBundleDisplayName", " 显示名称 "),
                    ("CFBundleName", "BundleName"),
                    ("CFBundleShortVersionString", " 7.24.9 "),
                    ("CFBundleVersion", "99"),
                    ("CFBundleIdentifier", "2dust.v2rayN"),
                ],
                binary,
            );
            let app = read_app(&path).unwrap();
            assert_eq!(app.version, "7.24.9");
            assert_eq!(app.name, "显示名称");
            assert_eq!(app.bundle_id, "2dust.v2rayN");
            assert_eq!(app.id, app.directory);
        }
    }

    #[test]
    fn falls_back_to_build_version_and_does_not_invent_unknown_versions() {
        let fixture = Fixture::new();
        let path = fixture.app(
            "Build.app",
            &[
                ("CFBundleShortVersionString", " "),
                ("CFBundleVersion", "123"),
                ("CFBundleDisplayName", " "),
                ("CFBundleName", "BuildName"),
            ],
            true,
        );
        let app = read_app(&path).unwrap();
        assert_eq!(app.version, "123");
        assert_eq!(app.name, "BuildName");
        let unknown = fixture.app("Unknown.app", &[], false);
        assert_eq!(read_app(&unknown).unwrap().version, "");
        std::fs::write(unknown.join("Contents/Info.plist"), "broken plist").unwrap();
        assert_eq!(read_app(&unknown).unwrap().name, "Unknown");
        assert_eq!(read_app(&unknown).unwrap().version, "");
        assert!(read_app(&fixture.0).is_none());
        assert!(read_app(&fixture.0.join("Missing.app")).is_none());
    }

    #[test]
    fn scans_nested_directories_without_hidden_apps_or_embedded_helpers() {
        let fixture = Fixture::new();
        fixture.app("Utilities/Company.Tools/Main.app", &[], false);
        fixture.app(
            "Utilities/Company.Tools/Main.app/Contents/Helpers/Helper.app",
            &[],
            false,
        );
        fixture.app(".Hidden.app", &[], false);
        fixture.app(".Hidden/Other.app", &[], false);
        fixture.app("Framework.framework/Internal.app", &[], false);
        let apps = scan_roots(&[fixture.0.clone(), fixture.0.join("Utilities")]);
        assert_eq!(apps.len(), 1);
        assert_eq!(apps[0].name, "Main");
    }

    #[cfg(unix)]
    #[test]
    fn deduplicates_symlinks_and_stops_directory_cycles() {
        let fixture = Fixture::new();
        let path = fixture.app("Main.app", &[], true);
        std::os::unix::fs::symlink(path, fixture.0.join("Link.app")).unwrap();
        std::os::unix::fs::symlink(&fixture.0, fixture.0.join("Cycle")).unwrap();
        assert_eq!(fixture.scan().len(), 1);
    }

    #[test]
    fn automatically_matches_names_and_repository_without_substring_matches() {
        let fixture = Fixture::new();
        fixture.app(
            "v2rayN.app",
            &[("CFBundleShortVersionString", "7.24.9")],
            true,
        );
        fixture.app(
            "Clash Verge.app",
            &[
                ("CFBundleName", "ClashVerge"),
                ("CFBundleShortVersionString", "2.4.2"),
            ],
            false,
        );
        let apps = fixture.scan();
        let mut records = vec![
            record("v2rayn", "https://github.com/2dust/v2rayn"),
            record("自定义名称", "https://github.com/example/Clash-Verge.git/"),
            record("Clash", "example/Clash"),
        ];
        assert!(refresh_records(&mut records, &apps));
        assert_eq!(records[0].installed_version, "7.24.9");
        assert_eq!(records[0].local, "7.24.9");
        assert_eq!(records[1].installed_version, "2.4.2");
        assert!(records[2].installed_dir.is_empty());
        assert!(!refresh_records(&mut records, &apps));
    }

    #[test]
    fn avoids_ambiguous_apps_with_identical_names() {
        let fixture = Fixture::new();
        fixture.app("First/Tool.app", &[], false);
        fixture.app("Second/Tool.app", &[], true);
        let apps = fixture.scan();
        assert_ne!(apps[0].id, apps[1].id);
        let mut records = vec![record("Tool", "example/Tool")];
        refresh_records(&mut records, &apps);
        assert!(records[0].installed_dir.is_empty());
    }

    #[test]
    fn preserves_manual_association_and_refreshes_versions_after_upgrades() {
        let fixture = Fixture::new();
        fixture.app(
            "Automatic.app",
            &[("CFBundleShortVersionString", "2")],
            false,
        );
        let path = fixture.app(
            "Custom/Manual.app",
            &[("CFBundleShortVersionString", "3")],
            true,
        );
        let mut records = vec![record("Automatic", "example/Automatic")];
        associate(&mut records[0], &read_app(&path).unwrap());
        records[0].installed_id = "Manual".to_string();
        records[0].installed_version = "1.0.0".to_string();
        fixture.app(
            "Custom/Manual.app",
            &[("CFBundleShortVersionString", "4")],
            true,
        );
        refresh_records(&mut records, &fixture.scan());
        assert_eq!(records[0].installed_name, "Manual");
        assert_eq!(records[0].installed_version, "4");
        assert_eq!(records[0].installed_id, read_app(&path).unwrap().id);
    }

    #[test]
    fn clears_stale_versions_when_an_app_is_removed() {
        let fixture = Fixture::new();
        let path = fixture.app("Tool.app", &[("CFBundleShortVersionString", "5")], false);
        let mut records = vec![record("Tool", "example/Tool")];
        refresh_records(&mut records, &fixture.scan());
        std::fs::remove_dir_all(path).unwrap();
        refresh_records(&mut records, &fixture.scan());
        assert!(records[0].installed_version.is_empty());
        assert!(records[0].local.is_empty());
        assert!(records[0].installed_dir.is_empty());
    }

    #[test]
    fn persists_explicit_unbind_and_allows_manual_rebinding() {
        let fixture = Fixture::new();
        let path = fixture.app("Tool.app", &[("CFBundleShortVersionString", "6")], false);
        let mut records = vec![record("Tool", "example/Tool")];
        assert!(!records[0].installed_auto_disabled);
        refresh_records(&mut records, &fixture.scan());
        unbind(&mut records[0]);
        records = serde_json::from_str(&serde_json::to_string(&records).unwrap()).unwrap();
        refresh_records(&mut records, &fixture.scan());
        assert!(records[0].installed_dir.is_empty());
        assert!(records[0].installed_auto_disabled);
        associate(&mut records[0], &read_app(&path).unwrap());
        refresh_records(&mut records, &fixture.scan());
        assert_eq!(records[0].installed_version, "6");
        assert!(!records[0].installed_auto_disabled);
    }
}
