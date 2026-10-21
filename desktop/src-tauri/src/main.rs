use serde_json::{json, Value};
use std::{
    collections::HashMap,
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStdin, Command, Stdio},
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        mpsc, Arc, Mutex,
    },
    time::Duration,
};
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Emitter, Manager,
};
use tauri_plugin_opener::OpenerExt;

struct Backend {
    input: Arc<Mutex<ChildStdin>>,
    child: Mutex<Child>,
    pending: Arc<Mutex<HashMap<u64, mpsc::Sender<Value>>>>,
    next_id: AtomicU64,
}

impl Backend {
    fn launch(app: &tauri::AppHandle, ui: bool, upstream: Option<Arc<Backend>>) -> Result<Arc<Self>, String> {
        let resources = app
            .path()
            .resource_dir()
            .map_err(|e| e.to_string())?
            .join("payload");
        let override_root = std::env::var_os("TOOLBOX_WORKSPACE").map(PathBuf::from);
        let use_data_home = override_root.is_none();
        let managed = !cfg!(debug_assertions) && use_data_home;
        let default_home = app.path().app_data_dir().map_err(|e| e.to_string())?;
        let app_home = std::env::var_os("TOOLBOX_APP_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                if cfg!(debug_assertions) {
                    default_home.join("development")
                } else {
                    default_home
                }
            });
        let root = if let Some(path) = override_root {
            path
        } else if cfg!(debug_assertions) {
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .unwrap()
                .parent()
                .unwrap()
                .to_path_buf()
        } else {
            app_home.join("app")
        };
        let runtime = resources.join("runtime");
        let bundled = runtime.join("python/bin/python3");
        let python = if bundled.is_file() {
            bundled
        } else if cfg!(debug_assertions) {
            root.join(".venv/bin/python")
        } else {
            return Err("内置 Python 不完整，请重新安装完整发行版。".into());
        };
        if !python.is_file() {
            return Err("Python 环境未初始化，请先运行 setup_macos.command。".into());
        }
        if managed && !ui {
            let mut setup = Command::new(&python);
            setup
                .args(["-I", "-B"])
                .arg(resources.join("bootstrap/prepare_workspace.py"))
                .arg(&resources)
                .arg(&app_home)
                .arg("--managed");
            let output = setup.output().map_err(|e| e.to_string())?;
            if !output.status.success() {
                return Err(String::from_utf8_lossy(&output.stderr).to_string());
            }
        } else if use_data_home && !ui {
            let mut setup = Command::new(&python);
            setup
                .args(["-B"])
                .arg(root.join("packaging/tauri/workspace_layout.py"))
                .arg(&app_home);
            let output = setup.output().map_err(|e| e.to_string())?;
            if !output.status.success() {
                return Err(String::from_utf8_lossy(&output.stderr).to_string());
            }
        }
        let gui_python = runtime.join("python/电力工具箱.app/Contents/MacOS/电力工具箱");
        let native_bundle = ui && cfg!(target_os = "macos") && gui_python.is_file();
        let mut command = Command::new(if native_bundle { &gui_python } else { &python });
        let entry = root.join(if ui { "desktop/python/native_bridge.py" } else { "desktop/python/service_bridge.py" });
        #[cfg(debug_assertions)]
        let entry = if ui { std::env::var_os("TOOLBOX_UI_TEST_ENTRY").map(PathBuf::from).unwrap_or(entry) } else { entry };
        command
            .args(["-u", "-B"])
            .arg(entry)
            .arg("--workspace")
            .arg(&root)
            .current_dir(&root)
            .env("PYTHONUTF8", "1")
            .env("PYTHONIOENCODING", "utf-8")
            .env("PYTHONNOUSERSITE", "1")
            .env("TOOLBOX_PYTHON", &python)
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .env("TOOLBOX_CODE_ROOT", &root)
            .env("TOOLBOX_APP_VERSION", app.package_info().version.to_string());
        if let Ok(executable) = std::env::current_exe() {
            if let Some(bundle) = executable.ancestors().find(|p| p.extension().is_some_and(|x| x == "app")) {
                command.env("TOOLBOX_APP_BUNDLE", bundle);
            }
        }
        if ui { command.arg("--remote-business"); }
        command
            .env_remove("PYTHONHOME")
            .env_remove("PYTHONPATH")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        if use_data_home {
            command.env("TOOLBOX_DATA_ROOT", app_home.join("data"));
        }
        if native_bundle {
            command.env("PYTHONHOME", runtime.join("python"))
                .env("__CFBundleIdentifier", "com.electricity.toolbox.native");
        }
        if runtime.is_dir() {
            command
                .env("TOOLBOX_PORTABLE", "1")
                .env("PLAYWRIGHT_BROWSERS_PATH", runtime.join("ms-playwright"))
                .env(
                    "TOOLBOX_NODE",
                    runtime.join("node/bin/node"),
                );
            let tcl = runtime.join("python/lib/tcl8.6");
            if tcl.is_dir() {
                command
                    .env("TCL_LIBRARY", tcl)
                    .env("TK_LIBRARY", runtime.join("python/lib/tk8.6"));
            }
        }
        let mut child = command
            .spawn()
            .map_err(|e| format!("无法启动 Python：{e}"))?;
        let input = Arc::new(Mutex::new(child.stdin.take().ok_or("Python stdin unavailable")?));
        let output = child.stdout.take().ok_or("Python stdout unavailable")?;
        let errors = child.stderr.take().ok_or("Python stderr unavailable")?;
        let pending: Arc<Mutex<HashMap<u64, mpsc::Sender<Value>>>> =
            Arc::new(Mutex::new(HashMap::new()));
        let responses = Arc::clone(&pending);
        let handle = app.clone();
        let reply_input = input.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(output).lines().map_while(Result::ok) {
                if let Ok(value) = serde_json::from_str::<Value>(&line) {
                    if let (Some(rpc), Some(service)) = (value.get("rpc"), upstream.as_ref()) {
                        let rpc = rpc.clone();
                        let service = service.clone();
                        let input = reply_input.clone();
                        std::thread::spawn(move || {
                            let result = service.request(rpc["method"].as_str().unwrap_or(""), rpc["params"].clone());
                            let reply = match result {
                                Ok(value) => json!({"rpc_reply": rpc["id"], "result": value}),
                                Err(error) => json!({"rpc_reply": rpc["id"], "error": error}),
                            };
                            let _ = input.lock().unwrap().write_all((reply.to_string() + "\n").as_bytes());
                        });
                    } else if let Some(id) = value.get("id").and_then(Value::as_u64) {
                        if let Some(sender) = responses.lock().unwrap().remove(&id) {
                            let _ = sender.send(value);
                        }
                    } else if let Some(event) = value.get("event") {
                        let _ = handle.emit("backend-event", event);
                        if ui && event["kind"] == "native-closed" {
                            finish_after_ui(&handle);
                        }
                    }
                }
            }
            responses.lock().unwrap().clear();
            if ui { finish_after_ui(&handle); }
            else if !handle.try_state::<State>().map(|s| s.native_closed.load(Ordering::Acquire)).unwrap_or(false) {
                let _ = handle.emit("backend-error", "业务后台已退出。请核对输出后重新启动；写入和上传不会自动重试。");
                show_updater(&handle);
            }
        });
        let handle = app.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(errors).lines().map_while(Result::ok) {
                let _ = handle.emit("backend-error", line);
            }
        });
        Ok(Arc::new(Self {
            input,
            child: Mutex::new(child),
            pending,
            next_id: AtomicU64::new(1),
        }))
    }

    fn request(&self, method: &str, params: Value) -> Result<Value, String> {
        if self
            .child
            .lock()
            .unwrap()
            .try_wait()
            .map_err(|e| e.to_string())?
            .is_some()
        {
            return Err("Python 任务服务已退出".into());
        }
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let (tx, rx) = mpsc::channel();
        self.pending.lock().unwrap().insert(id, tx);
        let payload = json!({"id":id,"method":method,"params":params}).to_string() + "\n";
        if let Err(error) = self.input.lock().unwrap().write_all(payload.as_bytes()) {
            self.pending.lock().unwrap().remove(&id);
            return Err(error.to_string());
        }
        let timeout = Duration::from_secs(30);
        let response = rx.recv_timeout(timeout).map_err(|e| {
            self.pending.lock().unwrap().remove(&id);
            e.to_string()
        })?;
        if let Some(error) = response.get("error") {
            Err(error.as_str().unwrap_or("Python 操作失败").into())
        } else {
            Ok(response["result"].clone())
        }
    }
}

struct State {
    backend: Result<Arc<Backend>, String>,
    ui: Result<Arc<Backend>, String>,
    native_closed: AtomicBool,
}

fn show_updater(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn finish_after_ui(app: &tauri::AppHandle) {
    let Some(state) = app.try_state::<State>() else { return; };
    if state.native_closed.swap(true, Ordering::AcqRel) { return; }
    let backend = state.backend.clone();
    let handle = app.clone();
    std::thread::spawn(move || {
        if let Ok(backend) = backend {
            // A crashed UI must not interrupt a business write. The service
            // refuses shutdown until non-interruptible tasks have completed.
            loop {
                if backend.request("shutdown", json!({"cancel": true})).is_ok() { break; }
                if backend.child.lock().unwrap().try_wait().ok().flatten().is_some() { break; }
                std::thread::sleep(Duration::from_millis(250));
            }
        }
        handle.exit(0);
    });
}

fn request_native(app: &tauri::AppHandle, method: &'static str) {
    let handle = app.clone();
    std::thread::spawn(move || {
        if let Some(state) = handle.try_state::<State>() {
            let result = state.ui.clone().and_then(|ui| ui.request(method, json!({})));
            if let Err(error) = result {
                let ui_stopped = state.ui.as_ref().map(|ui|
                    ui.child.lock().unwrap().try_wait().ok().flatten().is_some()
                ).unwrap_or(true);
                if ui_stopped && (method == "request_close" || method == "installer_close") {
                    finish_after_ui(&handle);
                    return;
                }
                let _ = handle.emit("backend-error", error);
                show_updater(&handle);
            }
        }
    });
}

#[tauri::command]
async fn native_activate(app: tauri::AppHandle) -> Result<(), String> {
    let ui = app.state::<State>().ui.clone()?;
    tauri::async_runtime::spawn_blocking(move || ui.request("activate", json!({})))
        .await.map_err(|e| e.to_string())??;
    if let Some(window) = app.get_webview_window("main") { let _ = window.hide(); }
    Ok(())
}

#[tauri::command]
async fn backend_call(
    method: String,
    params: Value,
    state: tauri::State<'_, State>,
) -> Result<Value, String> {
    let backend = state.backend.clone()?;
    tauri::async_runtime::spawn_blocking(move || backend.request(&method, params))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
fn open_path(path: String, app: tauri::AppHandle) -> Result<(), String> {
    let path = PathBuf::from(path);
    if !path.exists() {
        return Err("文件或目录不存在".into());
    }
    if !path.is_dir()
        && !["xlsx", "pdf", "png", "docx", "html", "txt", "md", "json", "dmg"].contains(
            &path
                .extension()
                .and_then(|x| x.to_str())
                .unwrap_or("")
                .to_lowercase()
                .as_str(),
        )
    {
        return Err("不支持直接打开此类文件".into());
    }
    app.opener()
        .open_path(path.to_string_lossy(), None::<&str>)
        .map_err(|e| e.to_string())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, args, _| {
            if args.iter().any(|arg| arg == "--show-updater") {
                request_native(app, "check_updates");
            } else if args.iter().any(|arg| arg == "--quit") {
                request_native(app, "installer_close");
            } else {
                request_native(app, "activate");
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let backend = Backend::launch(app.handle(), false, None);
            let ui = match &backend {
                Ok(service) => Backend::launch(app.handle(), true, Some(service.clone())),
                Err(error) => Err(error.clone()),
            };
            let failed = backend.is_err() || ui.is_err();
            app.manage(State {
                backend,
                ui,
                native_closed: AtomicBool::new(false),
            });
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Accessory);
            let open = MenuItem::with_id(app, "open", "打开工具箱", true, None::<&str>)?;
            let update = MenuItem::with_id(app, "update", "检查更新", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "退出工具箱", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &update, &quit])?;
            TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
                .tooltip("电力工具箱")
                .menu(&menu)
                .show_menu_on_left_click(true)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => request_native(app, "activate"),
                    "update" => {
                        request_native(app, "check_updates");
                    }
                    "quit" => request_native(app, "request_close"),
                    _ => {}
                })
                .build(app)?;
            if failed {
                show_updater(app.handle());
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            backend_call,
            open_path,
            native_activate
        ])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("无法启动电力工具箱")
        .run(|app, event| {
            #[cfg(target_os = "macos")]
            if matches!(&event, tauri::RunEvent::Ready) {
                let _ = app.set_activation_policy(tauri::ActivationPolicy::Accessory);
            }
            if let Some(state) = app.try_state::<State>() {
                if let Ok(backend) = &state.backend {
                    match event {
                        #[cfg(target_os = "macos")]
                        tauri::RunEvent::Reopen { .. } => request_native(app, "activate"),
                        tauri::RunEvent::ExitRequested { api, .. } => {
                            if !state.native_closed.load(Ordering::Acquire) {
                                api.prevent_exit();
                                request_native(app, "request_close");
                            }
                        }
                        tauri::RunEvent::Exit => {
                            // Normal shutdown is acknowledged by the business
                            // layer before the host exits. Do not kill writes.
                            let _ = backend.child.lock().unwrap().try_wait();
                        }
                        _ => {}
                    }
                }
            }
        });
}

#[cfg(test)]
mod service_tests;
