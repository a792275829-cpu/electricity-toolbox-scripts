//! Signed Tauri updates in the user's Applications folder; no privileged fallback.
use super::{Backend, State};
use serde_json::{json, Value};
use std::{
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};
use tauri::Manager;
use tauri_plugin_updater::UpdaterExt;

#[derive(PartialEq, Eq)]
enum Phase {
    Idle,
    Downloading,
    Installing,
}

pub struct UpdateJob {
    phase: Phase,
    generation: u64,
    task: Option<tauri::async_runtime::JoinHandle<()>>,
}
impl Default for UpdateJob {
    fn default() -> Self {
        Self {
            phase: Phase::Idle,
            generation: 0,
            task: None,
        }
    }
}
impl UpdateJob {
    pub fn installing(&self) -> bool {
        self.phase == Phase::Installing
    }
}

pub fn user_bundle(executable: &Path, home: &Path) -> Result<PathBuf, String> {
    use std::os::unix::fs::MetadataExt;
    let expected = home.join("Applications/电力工具箱.app");
    let bundle = executable
        .ancestors()
        .find(|p| p.extension().is_some_and(|e| e == "app"))
        .ok_or("请先从 DMG 安装到用户应用程序目录，再更新")?;
    let parent = expected.parent().unwrap();
    if bundle != expected || expected.is_symlink() || parent.is_symlink() {
        return Err("请退出工具箱，打开 DMG 中的“安装到我的应用程序.command”，迁入 ~/Applications 后即可免管理员授权更新。".into());
    }
    let uid = home.metadata().map_err(|e| e.to_string())?.uid();
    if expected.metadata().map_err(|e| e.to_string())?.uid() != uid
        || parent.metadata().map_err(|e| e.to_string())?.uid() != uid
    {
        return Err("用户应用目录或应用不属于当前用户，请重新从 DMG 安装到用户应用程序目录".into());
    }
    let probe = parent.join(format!(".toolbox-update-check-{}", std::process::id()));
    std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&probe)
        .map_err(|e| format!("用户应用目录不可写：{e}"))?;
    std::fs::remove_file(probe).map_err(|e| e.to_string())?;
    Ok(expected)
}

fn backend(app: &tauri::AppHandle) -> Result<Arc<Backend>, String> {
    app.state::<State>().backend.clone()
}
fn notify(app: &tauri::AppHandle, method: &str, params: Value) {
    if let Ok(backend) = backend(app) {
        let _ = backend.notify(method, params);
    }
}

pub fn event(app: &tauri::AppHandle, event: &Value) {
    let state = app.state::<State>();
    if event["kind"] == "cancel_update" {
        let mut job = state.update_job.lock().unwrap();
        if job.phase == Phase::Downloading {
            if let Some(task) = job.task.take() {
                task.abort();
            }
            job.phase = Phase::Idle;
            notify(app, "update_cancelled", json!({}));
        }
        return;
    }
    let mut job = state.update_job.lock().unwrap();
    if job.phase != Phase::Idle {
        return;
    }
    job.phase = Phase::Downloading;
    job.generation += 1;
    let generation = job.generation;
    let handle = app.clone();
    let version = event["version"]
        .as_str()
        .unwrap_or("")
        .trim_start_matches('v')
        .to_string();
    job.task = Some(tauri::async_runtime::spawn(async move {
        let result = perform(&handle, &version, generation).await;
        if let Err(error) = result {
            let state = handle.state::<State>();
            let mut job = state.update_job.lock().unwrap();
            if job.generation == generation && job.phase != Phase::Idle {
                job.phase = Phase::Idle;
                notify(&handle, "update_failed", json!({"error": error}));
            }
        }
    }));
}

async fn perform(app: &tauri::AppHandle, version: &str, generation: u64) -> Result<(), String> {
    user_bundle(
        &std::env::current_exe().map_err(|e| e.to_string())?,
        &app.path().home_dir().map_err(|e| e.to_string())?,
    )?;
    let mut update = app
        .updater_builder()
        .timeout(Duration::from_secs(20))
        .build()
        .map_err(|e| e.to_string())?
        .check()
        .await
        .map_err(|e| format!("无法检查签名更新：{e}"))?
        .ok_or("目前没有可用更新")?;
    if update.version != version {
        return Err("版本信息已变化，请重新检查更新".into());
    }
    update.timeout = Some(Duration::from_secs(1800));
    let mut received = 0u64;
    let mut previous = None;
    // Tauri verifies the embedded public-key signature before returning any bytes.
    let bytes = update
        .download(
            |chunk, total| {
                received += chunk as u64;
                let percent = total
                    .filter(|n| *n > 0)
                    .map(|n| (received.saturating_mul(100) / n).min(100));
                if percent != previous {
                    previous = percent;
                    notify(
                        app,
                        "update_progress",
                        json!({"stage":"downloading", "percent":percent.unwrap_or(0)}),
                    );
                }
            },
            || {},
        )
        .await
        .map_err(|e| format!("下载或签名校验失败：{e}"))?;
    {
        let state = app.state::<State>();
        let mut job = state.update_job.lock().unwrap();
        if job.phase != Phase::Downloading || job.generation != generation {
            return Ok(());
        }
        job.phase = Phase::Installing;
    }
    let worker = backend(app)?;
    let guard = worker.clone();
    tauri::async_runtime::spawn_blocking(move || guard.request("prepare_update", json!({})))
        .await
        .map_err(|e| e.to_string())??;
    notify(
        app,
        "update_progress",
        json!({"stage":"installing", "percent":100}),
    );
    // Recheck the writable user-only location immediately before replacement.
    user_bundle(
        &std::env::current_exe().map_err(|e| e.to_string())?,
        &app.path().home_dir().map_err(|e| e.to_string())?,
    )?;
    tauri::async_runtime::spawn_blocking(move || {
        update
            .install(bytes)
            .map_err(|e| format!("更新未完成：{e}"))
    })
    .await
    .map_err(|e| e.to_string())??;
    tauri::async_runtime::spawn_blocking(move || -> Result<(), String> {
        worker.request("finish_update", json!({}))?;
        for _ in 0..100 {
            if worker
                .child
                .lock()
                .unwrap()
                .try_wait()
                .map_err(|e| e.to_string())?
                .is_some()
            {
                return Ok(());
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        Err("新版已安装，旧窗口未退出，请退出后重新打开工具箱".into())
    })
    .await
    .map_err(|e| e.to_string())??;
    app.restart();
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn protected_and_mounted_locations_never_reach_installer() {
        for path in [
            "/Applications/电力工具箱.app/Contents/MacOS/electricity-toolbox",
            "/Volumes/工具箱/电力工具箱.app/Contents/MacOS/electricity-toolbox",
        ] {
            assert!(user_bundle(Path::new(path), Path::new("/Users/test")).is_err());
        }
    }
}
