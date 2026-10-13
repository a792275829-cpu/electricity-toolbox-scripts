import { describe, it, expect, vi } from "vitest";
import { checkForUpdate, type UpdateActions, type UpdateStatus } from "./update-check";
import type { DownloadEvent } from "@tauri-apps/plugin-updater";

function fixture() {
  const update = { version: "0.1.3", close: vi.fn(async () => {}),
    downloadAndInstall: vi.fn(async (progress?: (event: DownloadEvent) => void) => {
      progress?.({ event: "Started", data: { contentLength: 100 } });
      progress?.({ event: "Progress", data: { chunkLength: 42 } });
      progress?.({ event: "Finished" });
    }) };
  const actions: UpdateActions = {
    check: vi.fn(async () => update), prompt: vi.fn(async () => true),
    message: vi.fn(async () => {}), status: vi.fn(async (_status: UpdateStatus) => {}),
    reserve: vi.fn(async () => {}), restart: vi.fn(async () => {}),
    release: vi.fn(async () => {}),
  };
  return { actions, update };
}
describe("compact update interaction", () => {
  it("returns to idle after one failed check without downloading or reserving the app", async () => {
    const { actions } = fixture();
    actions.check = vi.fn(async () => { throw Error("network unavailable"); });
    await checkForUpdate(actions);
    expect(actions.check).toHaveBeenCalledOnce();
    expect(actions.prompt).not.toHaveBeenCalled();
    expect(actions.reserve).not.toHaveBeenCalled();
    expect(actions.restart).not.toHaveBeenCalled();
    expect(actions.message).toHaveBeenCalledExactlyOnceWith("检查更新失败，请稍后重试", true);
    expect(actions.status).toHaveBeenLastCalledWith({ phase: "idle" });
  });
  it("shows only the latest-version notice when no update is available", async () => {
    const { actions } = fixture();
    actions.check = vi.fn(async () => null);
    await checkForUpdate(actions);
    expect(actions.message).toHaveBeenCalledExactlyOnceWith("当前已是最新版本");
    expect(actions.prompt).not.toHaveBeenCalled();
    expect(actions.reserve).not.toHaveBeenCalled();
    expect(actions.status).toHaveBeenLastCalledWith({ phase: "idle" });
  });
  it("asks once and does not download if the user declines", async () => {
    const { actions, update } = fixture();
    actions.prompt = vi.fn(async () => false);
    await checkForUpdate(actions);
    expect(actions.prompt).toHaveBeenCalledExactlyOnceWith("0.1.3");
    expect(update.downloadAndInstall).not.toHaveBeenCalled();
    expect(update.close).toHaveBeenCalledOnce();
  });
  it("reports download and install progress in the original footer", async () => {
    const { actions, update } = fixture();
    await checkForUpdate(actions);
    expect(update.downloadAndInstall).toHaveBeenCalledOnce();
    expect(actions.status).toHaveBeenCalledWith({ phase: "downloading", progress: 0 });
    expect(actions.status).toHaveBeenCalledWith({ phase: "installing" });
    expect(actions.restart).toHaveBeenCalledOnce();
    expect(actions.release).toHaveBeenCalledOnce();
  });
  it("keeps the old app usable and shows one short notice after a signature failure", async () => {
    const { actions, update } = fixture();
    update.downloadAndInstall.mockRejectedValueOnce(Error("bad signature"));
    await checkForUpdate(actions);
    expect(actions.restart).not.toHaveBeenCalled();
    expect(actions.release).toHaveBeenCalledOnce();
    expect(actions.message).toHaveBeenCalledExactlyOnceWith("更新失败，请稍后重试", true);
    expect(actions.status).toHaveBeenLastCalledWith({ phase: "idle" });
  });
  it("shows an active-business-task explanation without starting a download", async () => {
    const { actions, update } = fixture();
    actions.reserve = vi.fn(async () => { throw "请等待正在运行的任务完成后更新"; });
    await checkForUpdate(actions);
    expect(update.downloadAndInstall).not.toHaveBeenCalled();
    expect(actions.message).toHaveBeenCalledExactlyOnceWith("请等待正在运行的任务完成后更新", true);
  });
});
