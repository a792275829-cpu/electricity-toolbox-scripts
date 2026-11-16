import { useEffect, useRef, useState, type FormEvent } from "react";
import { invoke } from "@tauri-apps/api/core";
import { call } from "./api";
import "./pilot.css";

type Task = {
  id: string;
  tool_id: string;
  state: string;
  cancellable: boolean;
  message: string;
  error: string | null;
  result: { message?: string; outputs?: string[] } | null;
};
type CatalogItem = { id: string; fields: { key: string; default: string }[] };
type Settings = { output_dir: string; output_exists: boolean };
type EventBatch = {
  cursor: number;
  events: { task_id: string; kind: string; message?: string }[];
};
const terminal = new Set(["succeeded", "failed", "cancelled"]);
const stateNames: Record<string, string> = {
  created: "等待执行",
  cancelling: "正在取消",
  running: "正在导出",
  succeeded: "导出完成",
  failed: "执行失败",
  cancelled: "已取消",
};

export function OnlineEnergyPilot({ fixture }: { fixture: boolean }) {
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [settings, setSettings] = useState<Settings | null>(null);
  const [connected, setConnected] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [task, setTask] = useState<Task | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("请选择导出日期范围。");
  const request = useRef<{ signature: string; id: string } | null>(null);
  const errorBox = useRef<HTMLDivElement>(null);
  const startInput = useRef<HTMLInputElement>(null);
  const endInput = useRef<HTMLInputElement>(null);
  const busy = submitting || (!!taskId && (!task || !terminal.has(task.state)));

  useEffect(() => {
    let disposed = false;
    void Promise.all([
      call<CatalogItem[]>("catalog"),
      call<Settings>("online_energy.settings"),
      call<Task[]>("tasks"),
    ])
      .then(([catalog, config, tasks]) => {
        if (disposed) return;
        const fields = catalog.find(
          (item) => item.id === "online-energy",
        )?.fields;
        if (!fields) throw new Error("业务服务未提供导出页面。");
        setStart(
          fields.find((field) => field.key === "start_date")?.default ?? "",
        );
        setEnd(fields.find((field) => field.key === "end_date")?.default ?? "");
        setSettings(config);
        const active = tasks.find(
          (item) =>
            item.tool_id === "online-energy" && !terminal.has(item.state),
        );
        if (active) {
          setTask(active);
          setTaskId(active.id);
        }
        setConnected(true);
      })
      .catch((e) => {
        if (!disposed) setError(String(e));
      });
    return () => {
      disposed = true;
    };
  }, []);

  useEffect(() => {
    if (!taskId) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    let cursor = 0;
    async function poll() {
      try {
        const [tasks, batch] = await Promise.all([
          call<Task[]>("tasks"),
          call<EventBatch>("events", { after: cursor }),
        ]);
        if (disposed) return;
        cursor = batch.cursor;
        const messages = batch.events
          .filter(
            (event) =>
              event.task_id === taskId && event.kind === "log" && event.message,
          )
          .map((event) => event.message!);
        if (messages.length)
          setLogs((previous) => [...previous, ...messages].slice(-300));
        const current = tasks.find((item) => item.id === taskId);
        if (!current) throw new Error("未找到任务记录，请核对输出后重新连接。");
        setTask(current);
        if (terminal.has(current.state)) {
          setCancelling(false);
          request.current = null;
          if (current.state === "failed" && current.error)
            setError(current.error);
          setNotice(
            current.result?.message ||
              stateNames[current.state] ||
              current.state,
          );
          return;
        }
      } catch (e) {
        if (disposed) return;
        setError(`读取状态失败，后台任务可能仍在执行：${String(e)}`);
      }
      if (!disposed)
        timer = setTimeout(() => {
          void poll();
        }, 350);
    }
    void poll();
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [taskId]);

  async function startExport(event: FormEvent) {
    event.preventDefault();
    if (busy || !connected) return;
    setSubmitting(true);
    setError("");
    try {
      // Read the native form values: assistive technology may edit a WebKit
      // field without emitting React's synthetic onChange event.
      const values = {
        start_date: startInput.current?.value ?? "",
        end_date: endInput.current?.value ?? "",
      };
      const prepared = await call<{
        values: { start_date: string; end_date: string };
      }>("prepare_task", {
        tool_id: "online-energy",
        action: "run",
        values,
      });
      setStart(prepared.values.start_date);
      setEnd(prepared.values.end_date);
      if (startInput.current)
        startInput.current.value = prepared.values.start_date;
      if (endInput.current) endInput.current.value = prepared.values.end_date;
      const signature = JSON.stringify(prepared.values);
      if (request.current?.signature !== signature)
        request.current = { signature, id: crypto.randomUUID() };
      const id = await call<string>("start", {
        tool_id: "online-energy",
        action: "run",
        values: prepared.values,
        request_id: request.current!.id,
      });
      setLogs([]);
      setTask(null);
      setTaskId(id);
      setNotice("任务已提交，正在导出…");
    } catch (e) {
      setError(String(e));
      setTimeout(() => errorBox.current?.focus(), 0);
    } finally {
      setSubmitting(false);
    }
  }

  async function cancel() {
    if (!taskId || cancelling) return;
    setCancelling(true);
    try {
      const accepted = await call<boolean>("cancel", { task_id: taskId });
      setNotice(
        accepted ? "正在取消，请等待任务停止…" : "任务已结束，正在刷新状态…",
      );
    } catch (e) {
      setError(String(e));
      setCancelling(false);
    }
  }

  async function openPath(path: string) {
    try {
      await invoke("open_path", { path });
    } catch (e) {
      setError(String(e));
    }
  }

  const status = busy ? (cancelling ? "正在取消…" : "正在导出…") : notice;
  return (
    <div className="web-pilot">
      <a className="skip-link" href="#export-page">
        跳到导出设置
      </a>
      <aside className="pilot-sidebar">
        <div className="pilot-brand">
          <span className="brand-mark" aria-hidden="true">
            电
          </span>
          <span>
            电力工作台<small>工具箱 · 界面试用</small>
          </span>
        </div>
        <nav aria-label="工具模块">
          <p className="nav-category">数据采集</p>
          <a
            className="selected-module"
            href="#export-page"
            aria-current="page"
          >
            导出上网电量 <span aria-hidden="true">↗</span>
          </a>
        </nav>
        <p className="sidebar-note">
          一个模块，一次验证。
          <br />
          其余工具继续使用现有界面。
        </p>
      </aside>
      <main id="export-page" className="pilot-main">
        <header className="pilot-header">
          <div>
            <p className="pilot-breadcrumb">数据采集 / 导出上网电量</p>
            <h1>导出上网电量</h1>
            <p className="pilot-subtitle">
              按运行日期逐日导出，每天生成一个汇总工作簿。
            </p>
          </div>
          <span className={`connection ${connected ? "ready" : ""}`}>
            {connected ? "业务服务已连接" : "正在连接业务服务"}
          </span>
        </header>
        {fixture && (
          <p className="sample-notice">
            样例模式：使用内置测试数据生成真实 Excel，不访问网站。每次最多导出 3
            天。
          </p>
        )}
        <form
          onSubmit={(event) => {
            void startExport(event);
          }}
          noValidate
        >
          <fieldset className="pilot-panel date-panel">
            <legend>导出设置</legend>
            <div className="date-fields">
              <div>
                <label htmlFor="online-start-date">导出开始日期</label>
                <input
                  id="online-start-date"
                  type="text"
                  inputMode="numeric"
                  autoComplete="off"
                  spellCheck={false}
                  name="start_date"
                  ref={startInput}
                  defaultValue={start}
                  disabled={!connected || busy}
                  aria-describedby="date-help"
                />
              </div>
              <span className="date-separator" aria-hidden="true">
                至
              </span>
              <div>
                <label htmlFor="online-end-date">导出结束日期</label>
                <input
                  id="online-end-date"
                  type="text"
                  inputMode="numeric"
                  autoComplete="off"
                  spellCheck={false}
                  name="end_date"
                  ref={endInput}
                  defaultValue={end}
                  disabled={!connected || busy}
                  aria-describedby="date-help"
                />
              </div>
            </div>
            <p id="date-help" className="field-help">
              格式 YYYY-MM-DD。包含开始日和结束日，默认导出昨天。
            </p>
            <div className="pilot-actions">
              <button
                id="online-export"
                className="primary"
                type="submit"
                disabled={!connected || busy}
              >
                {submitting ? "正在提交…" : "开始导出"}
              </button>
              <button
                id="online-output"
                type="button"
                disabled={!settings?.output_exists}
                onClick={() => {
                  if (settings) void openPath(settings.output_dir);
                }}
              >
                打开输出目录
              </button>
              <button
                id="online-cancel"
                type="button"
                disabled={!busy || !task?.cancellable || cancelling}
                onClick={() => {
                  void cancel();
                }}
              >
                取消任务
              </button>
            </div>
          </fieldset>
        </form>
        {error && (
          <div ref={errorBox} tabIndex={-1} className="error" role="alert">
            {error}
          </div>
        )}
        <section
          className="pilot-panel result-panel"
          aria-labelledby="result-heading"
        >
          <div className="panel-heading">
            <h2 id="result-heading">任务与输出</h2>
            <span className={`task-badge ${task?.state ?? "idle"}`}>
              {task ? stateNames[task.state] || task.state : "待开始"}
            </span>
          </div>
          <output
            id="online-status"
            aria-label="导出任务状态"
            role="status"
            aria-live="polite"
            aria-atomic="true"
          >
            {status}
          </output>
          {busy && <progress aria-label="导出进度" />}
          {!!task?.result?.outputs?.length && (
            <ul className="output-files" aria-label="导出文件">
              {task.result.outputs.map((path) => (
                <li key={path}>
                  <span aria-hidden="true" className="file-icon">
                    XLSX
                  </span>
                  <div>
                    <strong>{path.split("/").pop()}</strong>
                    <small>{path}</small>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      void openPath(path);
                    }}
                    aria-label={`打开${path.split("/").pop()}`}
                  >
                    打开文件
                  </button>
                </li>
              ))}
            </ul>
          )}
          <details className="pilot-logs">
            <summary>
              运行日志{logs.length ? `（${logs.length} 条）` : ""}
            </summary>
            <pre id="online-log" tabIndex={0} aria-label="运行日志">
              {logs.join("\n") || "尚无运行日志。"}
            </pre>
          </details>
        </section>
        <footer className="pilot-footer">
          {settings?.output_dir ? (
            <>
              输出目录：<span>{settings.output_dir}</span>
            </>
          ) : (
            "正在读取输出设置…"
          )}
        </footer>
      </main>
    </div>
  );
}
