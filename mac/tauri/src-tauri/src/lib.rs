mod commands;
mod github_search;
mod installed_apps;
mod native_tasks;
mod state;

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .manage(state::AppState::default())
        .invoke_handler(tauri::generate_handler![
            commands::get_state,
            commands::save_settings,
            commands::choose_directory,
            commands::open_path,
            commands::project_action,
            commands::open_project_page,
            commands::copy_text,
            commands::software_action,
            commands::get_job,
            commands::cancel_job,
            commands::macos_doctor,
            commands::scan_installed_apps,
            commands::get_catalog,
            commands::check_manager_update
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
