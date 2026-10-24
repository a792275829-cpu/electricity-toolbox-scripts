use super::*;

#[test]
fn real_python_protocol_starts_without_a_gui_and_shuts_down_cleanly() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().parent().unwrap();
    let venv = root.join(".venv/bin/python");
    let python = if venv.exists() { venv } else { PathBuf::from("python3") };
    let folder = tempfile::tempdir().unwrap();
    let mut child = Command::new(python)
        .arg("-B").arg(root.join("desktop/python/service_bridge.py"))
        .arg("--workspace").arg(folder.path())
        .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped())
        .spawn().unwrap();
    {
        let mut input = child.stdin.take().unwrap();
        input.write_all(b"{\"id\":1,\"method\":\"ping\"}\n{\"id\":2,\"method\":\"shutdown\"}\n").unwrap();
    }
    let output = child.wait_with_output().unwrap();
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    let rows: Vec<Value> = String::from_utf8(output.stdout).unwrap().lines()
        .map(|line| serde_json::from_str(line).unwrap()).collect();
    let reply = rows.iter().find(|row| row["id"] == 1).unwrap();
    assert_eq!(reply["result"]["protocol"], 3);
    assert_eq!(reply["result"]["ui"], "headless");
    assert_eq!(rows.last().unwrap()["event"]["kind"], "service-closed");
}

#[test]
fn backend_reports_a_dead_worker_without_writing_to_its_pipe() {
    let mut child = Command::new("/bin/sh").args(["-c", "exit 7"])
        .stdin(Stdio::piped()).spawn().unwrap();
    let input = child.stdin.take().unwrap();
    child.wait().unwrap();
    let backend = Backend { input: Arc::new(Mutex::new(input)), child: Mutex::new(child),
        pending: Arc::new(Mutex::new(HashMap::new())), next_id: AtomicU64::new(1) };
    assert_eq!(backend.request("start", json!({})).unwrap_err(), "Python 任务服务已退出");
    assert!(backend.pending.lock().unwrap().is_empty());
}

#[test]
#[cfg(target_os = "macos")]
fn delta_update_rpc_installs_and_relaunches_a_signed_isolated_app() {
    run_delta_update_rpc(false);
}

#[test]
#[cfg(target_os = "macos")]
#[ignore = "requires local v0.1.14 release artifacts and an installed v0.1.13 app"]
fn production_delta_update_rpc_prepares_verified_image_without_installing() {
    run_delta_update_rpc(true);
}

#[cfg(target_os = "macos")]
fn run_delta_update_rpc(production: bool) {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().parent().unwrap();
    let temporary = tempfile::tempdir().unwrap();
    let folder = std::env::var_os("TOOLBOX_UPDATE_E2E_DIR")
        .map(PathBuf::from).unwrap_or_else(|| temporary.path().join("run"));
    std::fs::create_dir_all(&folder).unwrap();
    let entry = root.join(if production {
        "desktop/tests/python/update_rpc_production_entry.py"
    } else {
        "desktop/tests/python/update_rpc_e2e_entry.py"
    });
    let python = root.join(".venv/bin/python");
    let log = std::fs::File::create(folder.join("service.log")).unwrap();
    let mut child = Command::new(&python).arg("-u").arg(&entry).arg(&folder)
        .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::from(log))
        .spawn().unwrap();
    let input = child.stdin.take().unwrap();
    let output = child.stdout.take().unwrap();
    let pending: Arc<Mutex<HashMap<u64, mpsc::Sender<Value>>>> = Arc::new(Mutex::new(HashMap::new()));
    let responses = pending.clone();
    let invalid = Arc::new(Mutex::new(Vec::<String>::new()));
    let invalid_rows = invalid.clone();
    // Use the production JSON line parsing and response routing behavior.
    let reader = std::thread::spawn(move || {
        for line in BufReader::new(output).lines().map_while(Result::ok) {
            match serde_json::from_str::<Value>(&line) {
                Ok(value) => {
                    if let Some(id) = value.get("id").and_then(Value::as_u64) {
                        if let Some(sender) = responses.lock().unwrap().remove(&id) {
                            let _ = sender.send(value);
                        }
                    }
                }
                Err(_) => invalid_rows.lock().unwrap().push(line),
            }
        }
        responses.lock().unwrap().clear();
    });
    let backend = Backend { input: Arc::new(Mutex::new(input)), child: Mutex::new(child),
        pending, next_id: AtomicU64::new(1) };
    let started = std::time::Instant::now();
    let mut polls = 0;
    let mut progress = Vec::new();
    let outcome = (|| -> Result<(), String> {
        backend.request("ping", json!({}))?;
        backend.request("updates.check", json!({}))?;
        loop {
            let status = backend.request("updates.status", json!({}))?;
            if status["checking"] == false {
                if status["release"]["version"] != "v0.1.14" {
                    return Err(format!("unexpected release: {status}"));
                }
                break;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        backend.request("updates.reserve", json!({}))?;
        let mut wait = |id: Value| -> Result<Value, String> {
            let deadline = std::time::Instant::now() + Duration::from_secs(120);
            let mut cursor = json!(0);
            loop {
                polls += 1;
                let batch = backend.request("events", json!({"after":cursor}))?;
                cursor = batch["cursor"].clone();
                for event in batch["events"].as_array().ok_or("missing events")? {
                    if event["task_id"] == id && event["kind"] == "progress" {
                        progress.push(event["progress"].clone());
                    }
                }
                let tasks = backend.request("tasks", json!({}))?;
                let task = tasks.as_array().ok_or("missing tasks")?.iter()
                    .find(|task| task["id"] == id).ok_or("missing update task")?;
                match task["state"].as_str() {
                    Some("succeeded") => return Ok(task["result"].clone()),
                    Some("failed" | "cancelled") => return Err(task.to_string()),
                    _ => {}
                }
                if std::time::Instant::now() > deadline { return Err("task timed out".into()); }
                std::thread::sleep(Duration::from_millis(50));
            }
        };
        let id = backend.request("updates.download", json!({"version":"v0.1.14"}))?;
        let downloaded = wait(id)?;
        let path = downloaded["outputs"][0].clone();
        let id = backend.request("updates.prepare_install", json!({"version":"v0.1.14", "path":path}))?;
        let ready = wait(id)?;
        if ready["ready"] != true { return Err(format!("installer not ready: {ready}")); }
        if production {
            backend.request("e2e.verify_image", json!({}))?;
            backend.request("updates.abort", json!({}))?;
            backend.request("e2e.verify_cleanup", json!({}))?;
            backend.request("shutdown", json!({}))?;
            backend.child.lock().unwrap().wait().map_err(|error| error.to_string())?;
            return Ok(());
        }
        backend.request("updates.commit_install", json!({}))?;
        backend.request("shutdown", json!({}))?;
        backend.child.lock().unwrap().wait().map_err(|error| error.to_string())?;
        let deadline = std::time::Instant::now() + Duration::from_secs(30);
        while !folder.join("release/0.1.14.launched").exists() {
            if std::time::Instant::now() > deadline { return Err("updated app did not relaunch".into()); }
            std::thread::sleep(Duration::from_millis(100));
        }
        let verified = Command::new(&python).arg(&entry).arg(&folder).arg("--verify")
            .output().map_err(|error| error.to_string())?;
        if !verified.status.success() { return Err(String::from_utf8_lossy(&verified.stderr).into()); }
        Ok(())
    })();
    // Always reap this isolated service, including the historical failure run.
    let mut child = backend.child.lock().unwrap();
    if child.try_wait().unwrap().is_none() { let _ = child.kill(); }
    let _ = child.wait();
    drop(child);
    reader.join().unwrap();
    let rows = invalid.lock().unwrap();
    let evidence = json!({"success":outcome.is_ok(), "error":outcome.as_ref().err(),
        "polls":polls, "progress":progress, "invalid_protocol_lines":*rows,
        "elapsed_seconds":started.elapsed().as_secs_f64()});
    std::fs::write(folder.join("transport.json"), serde_json::to_vec_pretty(&evidence).unwrap()).unwrap();
    assert!(outcome.is_ok(), "{evidence}");
    assert!(rows.is_empty(), "non-JSON output in service transport: {rows:?}");
    assert!(progress.iter().any(|value| value.as_f64() == Some(0.9)), "local DMG build was not reached");
    assert!(progress.iter().any(|value| value.as_f64() == Some(1.0)), "download task did not finish");
}
