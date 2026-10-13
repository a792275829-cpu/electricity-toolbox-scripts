import { describe, it, expect, vi } from "vitest";
import { installUpdate } from "./update-flow";
describe("update task guard", () => {
  it("blocks download and restart when a business write is active", async () => {
    const download = vi.fn(),
      restart = vi.fn(),
      release = vi.fn();
    await expect(
      installUpdate(
        async () => {
          throw Error("active write");
        },
        download,
        restart,
        release,
      ),
    ).rejects.toThrow("active write");
    expect(download).not.toHaveBeenCalled();
    expect(restart).not.toHaveBeenCalled();
    expect(release).not.toHaveBeenCalled();
  });
  it("unlocks tasks after a failed signature/download and does not restart", async () => {
    const reserve = vi.fn(),
      restart = vi.fn(),
      release = vi.fn();
    await expect(
      installUpdate(
        reserve,
        async () => {
          throw Error("bad signature");
        },
        restart,
        release,
      ),
    ).rejects.toThrow("bad signature");
    expect(reserve).toHaveBeenCalledOnce();
    expect(release).toHaveBeenCalledOnce();
    expect(restart).not.toHaveBeenCalled();
  });
  it("installs before restarting", async () => {
    const events: string[] = [];
    await installUpdate(
      async () => {
        events.push("reserve");
      },
      async () => {
        events.push("install");
      },
      async () => {
        events.push("restart");
      },
      async () => {
        events.push("release");
      },
    );
    expect(events).toEqual(["reserve", "install", "restart", "release"]);
  });
});
