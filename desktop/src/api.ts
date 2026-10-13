import { invoke, isTauri } from "@tauri-apps/api/core";
export async function call<T>(
  method: string,
  params: Record<string, unknown> = {},
): Promise<T> {
  if (!isTauri()) throw new Error("请通过桌面启动器打开工具箱。");
  return invoke<T>("backend_call", { method, params });
}
