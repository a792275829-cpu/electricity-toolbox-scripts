import { useEffect, useRef } from "react";
import { listen } from "@tauri-apps/api/event";
import { check } from "@tauri-apps/plugin-updater";
import { invoke } from "@tauri-apps/api/core";
import { call } from "./api";
import { checkForUpdate } from "./update-check";

// The webview stays hidden; dialogs and progress belong to the original Tk UI.
export default function Updates() {
  const busy = useRef(false);
  useEffect(() => {
    const unlisten = listen("check-updates", async () => {
      if (busy.current) return;
      busy.current = true;
      try {
        await checkForUpdate({
          check: () => check({ timeout: 20000 }),
          prompt: (version) => call<boolean>("update_prompt", { version }),
          message: (message, error) => call("update_message", { message, error }),
          status: (status) => call("update_status", status),
          reserve: () => call("prepare_update"),
          restart: () => invoke("restart_app"),
          release: () => call("abort_update").catch(() => {}),
        });
      } finally {
        busy.current = false;
      }
    });
    void unlisten.then(() => invoke("updater_ready")).catch(() => {});
    return () => { void unlisten.then((dispose) => dispose()); };
  }, []);
  return null;
}
