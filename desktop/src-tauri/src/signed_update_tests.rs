//! Exercise the same Tauri downloader/verifier/installer used by the Tk bridge.
use serde_json::json;
use std::{
    io::{Read, Write},
    net::TcpListener,
    time::Duration,
};
use tauri_plugin_updater::UpdaterExt;

fn exercise(tamper: bool, announced_version: &str) {
    let folder = tempfile::tempdir().unwrap();
    let app_dir = folder.path().join("电力工具箱.app");
    std::fs::create_dir_all(app_dir.join("Contents/MacOS")).unwrap();
    let exe = app_dir.join("Contents/MacOS/electricity-toolbox");
    std::fs::write(&exe, b"old executable").unwrap();
    std::fs::write(app_dir.join("Contents/marker.txt"), b"old application").unwrap();
    let data = folder.path().join("data.json");
    std::fs::write(&data, b"keep business data").unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let endpoint = format!("http://{}", listener.local_addr().unwrap());
    let signature = include_str!("../../tests/fixtures/signed-update.app.tar.gz.sig").trim();
    let metadata = json!({"version": announced_version, "notes":"fixture", "pub_date":"2026-10-20T00:00:00Z", "platforms":{"darwin-aarch64":{"url":format!("{endpoint}/app.tar.gz"),"signature":signature}}}).to_string();
    let mut archive = include_bytes!("../../tests/fixtures/signed-update.app.tar.gz").to_vec();
    if tamper {
        archive[20] ^= 1;
    }
    let server = std::thread::spawn(move || {
        for body in [metadata.into_bytes(), archive] {
            let (mut stream, _) = listener.accept().unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(10)))
                .unwrap();
            let mut request = [0; 8192];
            stream.read(&mut request).unwrap();
            write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                body.len()
            )
            .unwrap();
            stream.write_all(&body).unwrap();
        }
    });
    let mut context = tauri::test::mock_context(tauri::test::noop_assets());
    context
        .config_mut()
        .plugins
        .0
        .insert("updater".into(), json!({"requireSignedVersion":true, "pubkey": include_str!("../../tests/fixtures/signed-update.pub").trim()}));
    let app = tauri::test::mock_builder()
        .plugin(
            tauri_plugin_updater::Builder::new()
                .pubkey(include_str!("../../tests/fixtures/signed-update.pub").trim())
                .build(),
        )
        .build(context)
        .unwrap();
    tauri::async_runtime::block_on(async {
        let update = app
            .updater_builder()
            .executable_path(&exe)
            .target("darwin-aarch64")
            .endpoints(vec![format!("{endpoint}/latest.json").parse().unwrap()])
            .unwrap()
            .timeout(Duration::from_secs(10))
            .no_proxy()
            .build()
            .unwrap()
            .check()
            .await
            .unwrap()
            .unwrap();
        let result = update.download(|_, _| {}, || {}).await;
        if tamper || announced_version != "9.0.0" {
            assert!(
                result.is_err(),
                "tampered content or a replayed version must be rejected"
            );
            assert_eq!(
                std::fs::read(app_dir.join("Contents/marker.txt")).unwrap(),
                b"old application"
            );
        } else {
            update.install(result.unwrap()).unwrap();
            assert_eq!(
                std::fs::read(app_dir.join("Contents/marker.txt")).unwrap(),
                b"new application"
            );
        }
    });
    server.join().unwrap();
    assert_eq!(std::fs::read(data).unwrap(), b"keep business data");
}

#[test]
fn signed_archive_replaces_user_app_and_preserves_data() {
    exercise(false, "9.0.0");
}
#[test]
fn tampered_archive_cannot_replace_old_app() {
    exercise(true, "9.0.0");
}
#[test]
fn signature_bound_to_another_version_is_rejected() {
    exercise(false, "9.0.1");
}
