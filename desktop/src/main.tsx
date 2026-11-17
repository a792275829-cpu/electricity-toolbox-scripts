import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import { call } from "./api";
import "./style.css";
import { OnlineEnergyPilot } from "./OnlineEnergyPilot";
import { ToolboxApp } from "./toolbox/ToolboxApp";

function ApplicationHost() {
  const [error, setError] = useState("");
  const [pilot, setPilot] = useState<{
    fixture: boolean;
    replica?: boolean;
  } | null>(null);
  useEffect(() => {
    void invoke<{ fixture: boolean; replica?: boolean } | null>(
      "webview_pilot_config",
    )
      .then(setPilot)
      .catch((e) => setError(String(e)));
    void call("ping").catch((e) => setError(String(e)));
    const unlisten = listen<string>("backend-error", ({ payload }) =>
      setError(payload),
    );
    return () => {
      void unlisten.then((dispose) => dispose());
    };
  }, []);
  if (pilot?.replica)
    return <ToolboxApp sample={pilot.fixture} hostError={error} />;
  if (pilot) return <OnlineEnergyPilot fixture={pilot.fixture} />;
  return (
    <>
      {error && (
        <main className="updater-window">
          <div className="error" role="alert">
            {error}
          </div>
          <button
            onClick={() =>
              invoke("native_activate").catch((e) => setError(String(e)))
            }
          >
            返回工具箱
          </button>
        </main>
      )}
    </>
  );
}
createRoot(document.getElementById("root")!).render(<ApplicationHost />);
