import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { getVersion } from "@tauri-apps/api/app";
import { listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import { check } from "@tauri-apps/plugin-updater";
import Updates from "./Updates";
import { call } from "./api";
import "./style.css";

// The business UI is ToolboxApp itself. This hidden webview is only the updater.
function UpdateWindow() {
  const [version, setVersion] = useState("0.1.0");
  const [error, setError] = useState("");
  const [available, setAvailable] = useState("");
  useEffect(() => {
    getVersion()
      .then(setVersion)
      .catch((e) => setError(String(e)));
    call("ping").catch((e) => setError(String(e)));
    const unlisten = listen<string>("backend-error", ({ payload }) =>
      setError(payload),
    );
    if (import.meta.env.PROD)
      check({ timeout: 15000 })
        .then(async (update) => {
          if (update) {
            setAvailable(update.version);
            await update.close();
          }
        })
        .catch(() => {});
    return () => {
      unlisten.then((fn) => fn());
    };
  }, []);
  return (
    <main className="updater-window">
      <Updates version={version} onError={(e) => setError(String(e))} />
      {available && <p className="muted">可更新至 {available}</p>}
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}
      <button
        className="quiet"
        onClick={() =>
          invoke("native_activate").catch((e) => setError(String(e)))
        }
      >
        返回工具箱
      </button>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<UpdateWindow />);
