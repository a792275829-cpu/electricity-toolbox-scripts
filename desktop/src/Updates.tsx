import { useCallback, useEffect, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { check, type Update } from "@tauri-apps/plugin-updater";
import { invoke } from "@tauri-apps/api/core";
import { call } from "./api";
import { installUpdate } from "./update-flow";
export default function Updates({
  version,
  onError,
}: {
  version: string;
  onError: (error: unknown) => void;
}) {
  const [update, setUpdate] = useState<Update | null>(null),
    [busy, setBusy] = useState(false),
    [message, setMessage] = useState(""),
    [progress, setProgress] = useState<number | null>(null);
  const inspect = useCallback(async () => {
    setBusy(true);
    try {
      const found = await check({ timeout: 20000 });
      setUpdate(found);
      setMessage(found ? `发现新版本 ${found.version}` : "当前已是最新版本");
    } catch (e) {
      setMessage("更新检查失败，可稍后重试");
      onError(e);
    } finally {
      setBusy(false);
    }
  }, [onError]);
  useEffect(() => {
    const unlisten = listen("check-updates", () => void inspect());
    return () => { void unlisten.then((dispose) => dispose()); };
  }, [inspect]);
  async function install() {
    if (!update) return;
    setBusy(true);
    try {
      let received = 0,
        total = 0;
      await installUpdate(
        () => call("prepare_update"),
        () =>
          update.downloadAndInstall((event) => {
            if (event.event === "Started")
              total = event.data.contentLength ?? 0;
            if (event.event === "Progress") {
              received += event.data.chunkLength;
              setProgress(
                total ? Math.min(100, (received / total) * 100) : null,
              );
            }
          }),
        async () => {
          setMessage("安装完成，正在重启");
          await invoke("restart_app");
        },
        () => call("abort_update").catch(() => {}),
      );
    } catch (e) {
      onError(e);
      setMessage("更新未完成，当前版本仍可继续使用");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel">
      <div className="eyebrow">版本管理</div>
      <h2>应用更新</h2>
      <p>当前版本 {version} · Apple 芯片 Mac / Windows x64</p>
      {import.meta.env.DEV && (
        <p className="muted">
          开发模式直接读取源码；请在正式安装版中测试安装更新。
        </p>
      )}
      <p className="muted">
        从 GitHub Releases 获取签名更新，安装前会检查任务是否已结束。
      </p>
      <div className="toolbar">
        <button disabled={busy} onClick={inspect}>
          检查更新
        </button>
        {update && (
          <button
            className="primary"
            disabled={busy || import.meta.env.DEV}
            onClick={install}
          >
            下载、安装并重启
          </button>
        )}
      </div>
      {message && <p role="status">{message}</p>}
      {busy && progress !== null && (
        <progress max={100} value={progress} />
      )}{" "}
      {update?.body && <pre className="release-notes">{update.body}</pre>}
    </section>
  );
}
