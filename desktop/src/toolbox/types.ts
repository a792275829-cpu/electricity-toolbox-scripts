export type Values = Record<string, any>;
export type Tool = {
  id: string;
  name: string;
  category: string;
  fields: {
    key: string;
    label: string;
    kind: string;
    default: any;
    extensions?: string[];
    choices?: string[][];
  }[];
  actions: { id: string; label: string }[];
};
export type Task = {
  id: string;
  tool_id: string;
  name: string;
  state: string;
  cancellable: boolean;
  message: string;
  error: string | null;
  error_detail?: { login_kind?: string } | null;
  result: any;
};
export const needsManualLogin = (task?: Task) =>
  task?.state === "failed" && task.error_detail?.login_kind === "verification";

export type Bootstrap = {
  catalog: Tool[];
  initial?: Record<string, Values>;
  directories: Record<string, string>;
  version: string;
};
export const terminal = (task?: Task) =>
  !!task && ["succeeded", "failed", "cancelled"].includes(task.state);
export const taskLabels: Record<string, string> = {
  created: "等待",
  running: "运行中",
  cancelling: "取消中",
  succeeded: "完成",
  failed: "失败",
  cancelled: "取消",
};

export function wpsConnectionStatus(result: {
  state?: string;
  started?: boolean;
}): string {
  if (result.state === "logged_in") return "浏览器已连接";
  if (result.state === "logged_out") return "等待 WPS 登录";
  return result.started
    ? "专用浏览器已打开，待确认登录"
    : "尚未确认连接与登录状态";
}

export type EventBatch = {
  cursor: number;
  truncated: boolean;
  events: { task_id: string; kind: string; message: string }[];
};

export function restoreTaskLogs(
  history: Task[],
  batch: EventBatch,
): Record<string, string> {
  const logs: Record<string, string> = {};
  const add = (tool: string, text: string) => {
    logs[tool] = (logs[tool] ?? "") + text + "\n";
  };
  if (batch.truncated) {
    for (const tool of new Set(history.map((task) => task.tool_id)))
      add(tool, "较早日志已超出后台保留范围，以下为仍可恢复的记录。");
  }
  for (const event of batch.events) {
    const task = history.find((task) => task.id === event.task_id);
    if (task && event.kind === "log") add(task.tool_id, event.message);
  }
  for (const task of history.filter(terminal)) {
    add(
      task.tool_id,
      `${task.result?.message ?? taskLabels[task.state]}${task.error ? `：${task.error}` : ""}`,
    );
  }
  return logs;
}

export function copyTableRow(rows: Values[], index: number, config = false) {
  if (!Number.isInteger(index) || index < 0 || index >= rows.length)
    throw new Error("请选择有效的行");
  const cloned = structuredClone(rows[index]);
  if (config) cloned.name = `${cloned.name}（副本）`;
  const result = [...rows];
  result.splice(index + 1, 0, cloned);
  return { rows: result, selected: index + 1 };
}
