import { describe, expect, it } from "vitest";
import {
  wpsConnectionStatus,
  copyTableRow,
  restoreTaskLogs,
  type Task,
} from "./types";

describe("WPS live connection status", () => {
  it("does not claim a connection when the local profile has no login", () => {
    expect(wpsConnectionStatus({ state: "logged_out" })).toBe("等待 WPS 登录");
  });
  it("requires the server to confirm login before displaying connected", () => {
    expect(wpsConnectionStatus({ state: "logged_in" })).toBe("浏览器已连接");
    expect(wpsConnectionStatus({ state: "unknown" })).toBe(
      "尚未确认连接与登录状态",
    );
  });
  it("distinguishes opening Chrome from confirming a session", () => {
    expect(wpsConnectionStatus({ started: true })).toBe(
      "专用浏览器已打开，待确认登录",
    );
  });
});

describe("task logs after WebView reload", () => {
  const history: Task[] = [
    {
      id: "one",
      tool_id: "transaction-info",
      name: "交易系统信息更新",
      state: "failed",
      cancellable: false,
      message: "",
      result: null,
      error: "进程退出码 1：first failure",
    },
    {
      id: "two",
      tool_id: "online-energy",
      name: "导出上网电量",
      state: "running",
      cancellable: true,
      message: "",
      result: null,
      error: null,
    },
  ];
  it("restores the first exception and failed status without starting another task", () => {
    const logs = restoreTaskLogs(history, {
      cursor: 3,
      truncated: false,
      events: [
        { task_id: "one", kind: "log", message: "Traceback: first failure" },
        { task_id: "two", kind: "progress", message: "半程" },
      ],
    });
    expect(logs["transaction-info"]).toContain("Traceback: first failure");
    expect(logs["transaction-info"]).toContain("进程退出码 1");
    expect(logs["online-energy"]).toBeUndefined();
  });
  it("keeps tool logs separate and explicitly marks unavailable earlier history", () => {
    const logs = restoreTaskLogs(history, {
      cursor: 4,
      truncated: true,
      events: [{ task_id: "two", kind: "log", message: "真实采集" }],
    });
    expect(logs["online-energy"]).toContain("真实采集");
    expect(logs["online-energy"]).not.toContain("first failure");
    expect(logs["transaction-info"]).toContain("超出后台保留范围");
  });
});

describe("WPS copy selection", () => {
  it("inserts and selects the new config beside the original", () => {
    const rows = [
      { name: "原配置", regions: [{ source_sheet: "汇总" }] },
      { name: "后续配置", regions: [] },
    ];
    const result = copyTableRow(rows, 0, true);
    expect(result.rows.map((row) => row.name)).toEqual([
      "原配置",
      "原配置（副本）",
      "后续配置",
    ]);
    expect(result.selected).toBe(1);
    result.rows[result.selected].regions[0].source_sheet = "新工作表";
    expect(rows[0].regions[0].source_sheet).toBe("汇总");
  });
  it("copies a region with the same placement without introducing a name field", () => {
    const result = copyTableRow(
      [{ source_start: "A1" }, { source_start: "B1" }],
      0,
    );
    expect(result.rows.map((row) => row.source_start)).toEqual([
      "A1",
      "A1",
      "B1",
    ]);
    expect(result.rows[1]).not.toHaveProperty("name");
    expect(result.selected).toBe(1);
  });
});
