import type { Update, DownloadEvent } from "@tauri-apps/plugin-updater";
import { installUpdate } from "./update-flow";

export type UpdateStatus = {
  phase: "idle" | "checking" | "downloading" | "installing";
  progress?: number;
};
type Candidate = Pick<Update, "version" | "downloadAndInstall" | "close">;
export type UpdateActions = {
  check: () => Promise<Candidate | null>;
  prompt: (version: string) => Promise<boolean>;
  message: (message: string, error?: boolean) => Promise<unknown>;
  status: (status: UpdateStatus) => Promise<unknown>;
  reserve: () => Promise<unknown>;
  restart: () => Promise<void>;
  release: () => Promise<unknown>;
};

export async function checkForUpdate(actions: UpdateActions) {
  let update: Candidate | null = null;
  let stage = "checking";
  try {
    await actions.status({ phase: "checking" });
    update = await actions.check();
    if (!update) {
      await actions.message("当前已是最新版本");
      return;
    }
    if (!(await actions.prompt(update.version))) return;
    stage = "preparing";
    let received = 0, total = 0, last = 0;
    const progress = (event: DownloadEvent) => {
      if (event.event === "Started") total = event.data.contentLength ?? 0;
      if (event.event === "Progress") received += event.data.chunkLength;
      if (event.event === "Finished") {
        void actions.status({ phase: "installing" }).catch(() => {});
      } else if (event.event === "Started" || Date.now() - last >= 250) {
        last = Date.now();
        void actions.status({ phase: "downloading",
          ...(total ? { progress: Math.min(100, received / total * 100) } : {})
        }).catch(() => {});
      }
    };
    await installUpdate(actions.reserve, async () => {
      stage = "installing";
      await actions.status({ phase: "downloading", progress: 0 });
      await update!.downloadAndInstall(progress);
    }, actions.restart, actions.release);
  } catch (error) {
    const message = stage === "checking" ? "检查更新失败，请稍后重试"
      : stage === "preparing" ? String(error)
      : "更新失败，请稍后重试";
    await actions.message(message, true);
  } finally {
    await actions.status({ phase: "idle" }).catch(() => {});
    await update?.close().catch(() => {});
  }
}
