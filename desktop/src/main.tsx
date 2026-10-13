import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import Updates from "./Updates";
import { call } from "./api";
import "./style.css";

function ApplicationHost() {
  const [error, setError] = useState("");
  useEffect(() => {
    void call("ping").catch((e) => setError(String(e)));
    const unlisten = listen<string>("backend-error", ({ payload }) => setError(payload));
    return () => { void unlisten.then((dispose) => dispose()); };
  }, []);
  return <>
    <Updates />
    {error && <main className="updater-window">
      <div className="error" role="alert">{error}</div>
      <button onClick={() => invoke("native_activate").catch((e) => setError(String(e)))}>
        返回工具箱
      </button>
    </main>}
  </>;
}
createRoot(document.getElementById("root")!).render(<ApplicationHost />);
