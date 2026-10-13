#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

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
    input: Mutex<ChildStdin>,
    child: Mutex<Child>,
    pending: Arc<Mutex<HashMap<u64, mpsc::Sender<Value>>>>,
    next_id: AtomicU64,
}

impl Backend {
    fn launch(app: &tauri::AppHandle) -> Result<Arc<Self>, String> {
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
        let bundled = if cfg!(windows) {
            runtime.join("python/python.exe")
        } else {
            runtime.join("python/bin/python3")
        };
        let python = if bundled.is_file() {
            bundled
        } else if cfg!(debug_assertions) {
            if cfg!(windows) {
                root.join(".venv/Scripts/python.exe")
            } else {
                root.join(".venv/bin/python")
            }
        } else {
            return Err("内置 Python 不完整，请重新安装完整发行版。".into());
        };
        if !python.is_file() {
            return Err("Python 环境未初始化，请先运行 setup_macos.command。".into());
        }
        if managed {
            let mut setup = Command::new(&python);
            setup
                .args(["-I", "-B"])
                .arg(resources.join("bootstrap/prepare_workspace.py"))
                .arg(&resources)
                .arg(&app_home)
                .arg("--managed");
            no_console(&mut setup);
            let output = setup.output().map_err(|e| e.to_string())?;
            if !output.status.success() {
                return Err(String::from_utf8_lossy(&output.stderr).to_string());
            }
        } else if use_data_home {
            let mut setup = Command::new(&python);
            setup
                .args(["-B"])
                .arg(root.join("packaging/tauri/workspace_layout.py"))
                .arg(&app_home);
            no_console(&mut setup);
            let output = setup.output().map_err(|e| e.to_string())?;
            if !output.status.success() {
                return Err(String::from_utf8_lossy(&output.stderr).to_string());
            }
        }
        let gui_python = runtime.join("python/ToolboxUI.app/Contents/MacOS/ToolboxUI");
        let native_bundle =
            cfg!(target_os = "macos") && !cfg!(debug_assertions) && gui_python.is_file();
        let mut command = Command::new(if native_bundle { &gui_python } else { &python });
        command
            .args(["-u", "-B"])
            .arg(root.join("desktop/python/native_bridge.py"))
            .arg("--workspace")
            .arg(&root)
            .current_dir(&root)
            .env("PYTHONUTF8", "1")
            .env("PYTHONIOENCODING", "utf-8")
            .env("PYTHONNOUSERSITE", "1")
            .env("TOOLBOX_PYTHON", &python)
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .env("TOOLBOX_CODE_ROOT", &root)
            .env_remove("PYTHONHOME")
            .env_remove("PYTHONPATH")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        if use_data_home {
            command.env("TOOLBOX_DATA_ROOT", app_home.join("data"));
        }
        if native_bundle {
            command
                .env("PYTHONHOME", runtime.join("python"))
                .env("__CFBundleIdentifier", "com.electricity.toolbox.native");
        }
        if runtime.is_dir() {
            command
                .env("TOOLBOX_PORTABLE", "1")
                .env("PLAYWRIGHT_BROWSERS_PATH", runtime.join("ms-playwright"))
                .env(
                    "TOOLBOX_NODE",
                    runtime.join(if cfg!(windows) {
                        "node/node.exe"
                    } else {
                        "node/bin/node"
                    }),
                );
            let tcl = runtime.join("python/lib/tcl8.6");
            if tcl.is_dir() {
                command
                    .env("TCL_LIBRARY", tcl)
                    .env("TK_LIBRARY", runtime.join("python/lib/tk8.6"));
            }
        }
        no_console(&mut command);
        let mut child = command
            .spawn()
            .map_err(|e| format!("无法启动 Python：{e}"))?;
        let input = child.stdin.take().ok_or("Python stdin unavailable")?;
        let output = child.stdout.take().ok_or("Python stdout unavailable")?;
        let errors = child.stderr.take().ok_or("Python stderr unavailable")?;
        let pending: Arc<Mutex<HashMap<u64, mpsc::Sender<Value>>>> =
            Arc::new(Mutex::new(HashMap::new()));
        let responses = Arc::clone(&pending);
        let handle = app.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(output).lines().map_while(Result::ok) {
                if let Ok(value) = serde_json::from_str::<Value>(&line) {
                    if let Some(id) = value.get("id").and_then(Value::as_u64) {
                        if let Some(sender) = responses.lock().unwrap().remove(&id) {
                            let _ = sender.send(value);
                        }
                    } else if let Some(event) = value.get("event") {
                        let _ = handle.emit("backend-event", event);
                        if event["kind"] == "check-updates" {
                            show_updater(&handle);
                            let _ = handle.emit("check-updates", ());
                        }
                        if event["kind"] == "native-closed" {
                            if let Some(state) = handle.try_state::<State>() {
                                state.native_closed.store(true, Ordering::Release);
                                if !state.restarting.load(Ordering::Acquire) {
                                    handle.exit(0);
                                }
                            }
                        }
                    }
                }
            }
            responses.lock().unwrap().clear();
            if !handle
                .try_state::<State>()
                .map(|s| s.native_closed.load(Ordering::Acquire))
                .unwrap_or(false)
            {
                let _ = handle.emit("backend-error", "原版工具箱已意外退出，请重启应用。");
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
            input: Mutex::new(input),
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
        let response = rx.recv_timeout(Duration::from_secs(30)).map_err(|e| {
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

fn no_console(command: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    #[cfg(not(windows))]
    {
        let _ = command;
    }
}

struct State {
    backend: Result<Arc<Backend>, String>,
    native_closed: AtomicBool,
    restarting: AtomicBool,
}

fn show_updater(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn request_native(app: &tauri::AppHandle, method: &'static str) {
    let handle = app.clone();
    std::thread::spawn(move || {
        if let Some(state) = handle.try_state::<State>() {
            let result = state
                .backend
                .clone()
                .and_then(|b| b.request(method, json!({})));
            if let Err(error) = result {
                if method == "request_close"
                    && state
                        .backend
                        .as_ref()
                        .map(|b| b.child.lock().unwrap().try_wait().ok().flatten().is_some())
                        .unwrap_or(true)
                {
                    state.native_closed.store(true, Ordering::Release);
                    handle.exit(0);
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
    let backend = app.state::<State>().backend.clone()?;
    let shown =
        tauri::async_runtime::spawn_blocking(move || backend.request("activate", json!({})))
            .await
            .map_err(|e| e.to_string())??;
    if shown == true {
        if let Some(window) = app.get_webview_window("main") {
            let _ = window.hide();
        }
    }
    Ok(())
}

#[tauri::command]
async fn restart_app(app: tauri::AppHandle) -> Result<(), String> {
    let backend = app.state::<State>().backend.clone()?;
    app.state::<State>()
        .restarting
        .store(true, Ordering::Release);
    let result =
        tauri::async_runtime::spawn_blocking(move || backend.request("finish_update", json!({})))
            .await
            .map_err(|e| e.to_string())
            .and_then(|r| r);
    if let Err(error) = result {
        app.state::<State>()
            .restarting
            .store(false, Ordering::Release);
        return Err(error);
    }
    app.state::<State>()
        .native_closed
        .store(true, Ordering::Release);
    app.restart();
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
        && !["xlsx", "pdf", "png", "docx", "html", "txt", "md", "json"].contains(
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
                show_updater(app);
            } else if args.iter().any(|arg| arg == "--quit") {
                request_native(app, "request_close");
            } else {
                request_native(app, "activate");
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let backend = Backend::launch(app.handle());
            let failed = backend.is_err();
            app.manage(State {
                backend,
                native_closed: AtomicBool::new(false),
                restarting: AtomicBool::new(false),
            });
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Accessory);
            let open = MenuItem::with_id(app, "open", "打开工具箱", true, None::<&str>)?;
            let update = MenuItem::with_id(app, "update", "应用更新…", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "退出工具箱", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &update, &quit])?;
            TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
                .tooltip("电力工具箱")
                .menu(&menu)
                .show_menu_on_left_click(true)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => request_native(app, "activate"),
                    "update" => show_updater(app),
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
            native_activate,
            restart_app
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
            if let Some(state) = app.try_state::<State>() {
                if let Ok(backend) = &state.backend {
                    match event {
                        tauri::RunEvent::ExitRequested { api, .. } => {
                            if !state.native_closed.load(Ordering::Acquire) {
                                api.prevent_exit();
                                request_native(app, "request_close");
                            }
                        }
                        tauri::RunEvent::Exit => {
                            let _ = backend.child.lock().unwrap().kill();
                        }
                        _ => {}
                    }
                }
            }
        });
}
