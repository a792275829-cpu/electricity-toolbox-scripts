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
  result: any;
};
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
