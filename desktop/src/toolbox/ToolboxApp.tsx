import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { ask, message } from "@tauri-apps/plugin-dialog";
import { call } from "../api";
import { Button, Check, Modal, choosePath } from "./controls";
import { PageForms, TextField } from "./PageForms";
import { WpsEditor } from "./WpsEditor";
import { ManualMarket } from "./ManualMarket";
import {
  terminal,
  taskLabels,
  restoreTaskLogs,
  needsManualLogin,
  type EventBatch,
  type Bootstrap,
  type Task,
  type Tool,
  type Values,
} from "./types";
import "./toolbox.css";

async function copyText(text: string) {
  if (navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {}
  }
  const box = document.createElement("textarea");
  box.value = text;
  document.body.append(box);
  box.select();
  document.execCommand("copy");
  box.remove();
}
const stamp = () => new Date().toLocaleTimeString("zh-CN", { hour12: false });
const basename = (path: string) => path.split("/").pop() ?? path;

function Credentials({
  register,
  login,
  sample,
  loginAvailable,
  loginTask,
}: {
  register: (flush: (required?: boolean) => Promise<boolean>) => void;
  login: () => void;
  sample: boolean;
  loginAvailable: boolean;
  loginTask?: Task;
}) {
  const [pair, setPair] = useState({ username: "", password: "" });
  const latest = useRef(pair);
  latest.current = pair;
  const [show, setShow] = useState(false);
  const [state, setState] = useState<any>({
    text: "填写账号密码后自动保存",
    kind: "neutral",
  });
  const dirty = useRef(false);
  const box = useRef<HTMLDivElement>(null);
  const usernameInput = useRef<HTMLInputElement>(null);
  const passwordInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (usernameInput.current) usernameInput.current.value = pair.username;
  }, [pair.username]);
  useEffect(() => {
    if (passwordInput.current) passwordInput.current.value = pair.password;
  }, [pair.password]);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const generation = useRef(0);
  async function verify() {
    clearTimeout(timer.current);
    if (!(await flush(true, false))) return;
    try {
      setState(await call("ui.credentials.verify"));
    } catch {
      setState({ text: "✗ 验证失败，请重试", kind: "error", retry: true });
    }
  }
  async function flush(required = false, autoVerify = true) {
    clearTimeout(timer.current);
    const current = {
      username: usernameInput.current?.value ?? latest.current.username,
      password: passwordInput.current?.value ?? latest.current.password,
    };
    if (
      current.username !== latest.current.username ||
      current.password !== latest.current.password
    ) {
      latest.current = current;
      generation.current++;
      dirty.current = true;
      setPair(current);
    }
    if (!current.username.trim() || !current.password) {
      if (required)
        setState({ text: "账号和密码需填写完整，尚未保存", kind: "error" });
      return !required;
    }
    if (!dirty.current) return true;
    try {
      const revision = generation.current;
      const result = await call("ui.credentials.save", current);
      if (generation.current === revision) {
        dirty.current = false;
        setState(result);
        if (autoVerify && !box.current?.contains(document.activeElement))
          timer.current = setTimeout(() => {
            void verify();
          }, 500);
      }
      return true;
    } catch {
      setState({ text: "保存失败，请检查本机文件写入权限", kind: "error" });
      return false;
    }
  }
  register(flush);
  useEffect(() => {
    let disposed = false;
    void call<{ username: string; password: string }>("ui.credentials.load")
      .then((saved) => {
        if (disposed) return;
        latest.current = saved;
        setPair(saved);
        if (saved.username && saved.password) {
          setState({
            text: "账号已保存，待验证",
            kind: "neutral",
            retry: true,
          });
          timer.current = setTimeout(() => {
            void verify();
          }, 300);
        }
      })
      .catch(() =>
        setState({ text: "读取失败，请检查本机账号配置文件", kind: "error" }),
      );
    return () => {
      disposed = true;
      clearTimeout(timer.current);
    };
  }, []);
  useEffect(() => {
    if (!state.running) return;
    let disposed = false;
    let timeout: ReturnType<typeof setTimeout>;
    const poll = async () => {
      const result = await call<any>("ui.credentials.status");
      if (disposed) return;
      setState(result);
      if (result.running)
        timeout = setTimeout(() => {
          void poll();
        }, 100);
    };
    void poll();
    return () => {
      disposed = true;
      clearTimeout(timeout);
    };
  }, [state.running]);
  function edit(key: "username" | "password", value: string) {
    generation.current++;
    dirty.current = true;
    latest.current = { ...latest.current, [key]: value };
    setPair(latest.current);
    clearTimeout(timer.current);
    void call("ui.credentials.cancel");
    setState({ text: "账号已修改，等待自动保存", kind: "neutral" });
    timer.current = setTimeout(() => {
      void flush();
    }, 1000);
  }
  return (
    <div
      ref={box}
      className="credentials-panel"
      onBlur={() => {
        const needsVerification =
          dirty.current || state.text === "账号已保存，待验证";
        setTimeout(() => {
          if (!box.current?.contains(document.activeElement)) {
            void flush(false, false).then((ok) => {
              if (
                ok &&
                needsVerification &&
                !dirty.current &&
                latest.current.username &&
                latest.current.password
              ) {
                clearTimeout(timer.current);
                timer.current = setTimeout(() => {
                  void verify();
                }, 500);
              }
            });
          }
        }, 0);
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          void verify();
        }
      }}
    >
      <h2>清能网站账号</h2>
      <div className="account-fields">
        <label htmlFor="account-username">账号</label>
        <input
          id="account-username"
          ref={usernameInput}
          autoComplete="username"
          defaultValue={pair.username}
          onInput={(event) => edit("username", event.currentTarget.value)}
        />
        <label htmlFor="account-password">密码</label>
        <input
          id="account-password"
          ref={passwordInput}
          type={show ? "text" : "password"}
          autoComplete="current-password"
          defaultValue={pair.password}
          onInput={(event) => edit("password", event.currentTarget.value)}
        />
      </div>
      <div className="account-actions">
        <Check label="显示密码" value={show} onChange={setShow} />
        <span
          className={`account-status ${state.kind ?? "neutral"}`}
          role="status"
        >
          {state.text}
        </span>
      </div>
      {state.running && (
        <Button
          run={() => {
            void call("ui.credentials.cancel").then(setState);
          }}
        >
          停止验证
        </Button>
      )}
      {state.retry && !state.running && (
        <Button
          run={() => {
            void verify();
          }}
        >
          重试验证
        </Button>
      )}
      {!sample &&
        loginAvailable &&
        !state.running &&
        (state.kind === "manual" || needsManualLogin(loginTask)) && (
          <Button id="account-login" run={login}>
            打开登录窗口
          </Button>
        )}
    </div>
  );
}

function Diagnostics({ navigation }: { navigation: string }) {
  const [report, setReport] = useState<any>(null);
  const [selected, setSelected] = useState(0);
  const [copied, setCopied] = useState(false);
  async function refresh() {
    try {
      setReport(await call("ui.diagnostics"));
    } catch (error) {
      setReport({
        errors: 1,
        warnings: 0,
        time: "",
        summary: String(error),
        issues: [],
      });
    }
  }
  useEffect(() => {
    void refresh();
  }, []);
  return (
    <section className="diagnostic-panel">
      <div className="rail-header">
        <h2>运行诊断</h2>
        <Button
          run={() => {
            void refresh();
          }}
        >
          重新检查
        </Button>
      </div>
      <p className="diagnostic-health">
        {!report
          ? "正在检查…"
          : report.errors
            ? `× 发现 ${report.errors} 项异常`
            : report.warnings
              ? `! 环境可用，有 ${report.warnings} 项提醒`
              : "✓ 未发现问题"}
      </p>
      <p className="muted">
        {report?.warnings || report?.errors
          ? `${report.warnings} 提醒 · ${report.errors} 异常  ·  ${report.time}`
          : `检查于 ${report?.time ?? ""}`}
      </p>
      <p className="muted">模块切换：{navigation}</p>
      {!!report?.issues.length && (
        <>
          <select
            size={Math.min(3, report.issues.length)}
            aria-label="诊断检查项"
            value={selected}
            onChange={(event) => setSelected(Number(event.target.value))}
          >
            {report.issues.map((issue: any, index: number) => (
              <option key={index} value={index}>
                {issue.status === "error" ? "×" : "!"} {issue.name}
              </option>
            ))}
          </select>
          <p className="muted">{report.issues[selected]?.detail}</p>
        </>
      )}
      <div className="rail-two-buttons">
        <Button
          run={() => {
            void choosePath("diagnostics", "", [
              "xlsx",
              "xls",
              "pdf",
              "docx",
              "json",
              "md",
              "txt",
            ]).then(async (path) => {
              if (path) {
                const result = await call<any>("ui.diagnostics", { path });
                await message(result.summary, { title: "文件检查结果" });
              }
            });
          }}
        >
          检查文件…
        </Button>
        <Button
          run={() => {
            if (report)
              void copyText(report.summary).then(() => {
                setCopied(true);
                setTimeout(() => setCopied(false), 1800);
              });
          }}
        >
          {copied ? "已复制" : "复制报告"}
        </Button>
      </div>
    </section>
  );
}

function TaskResults({ task }: { task?: Task }) {
  const outputs: string[] = [
    ...new Set<string>(
      (task?.result?.outputs ?? []).filter(
        (path: unknown) => typeof path === "string" && path,
      ),
    ),
  ];
  const missing: string[] = task?.result?.missing ?? [];
  if (!outputs.length && !missing.length) return null;
  return (
    <section className="task-results" aria-label="本次结果">
      <span className="muted">本次结果</span>
      {outputs.map((path) => (
        <Button
          key={path}
          title={path}
          run={() => {
            void invoke("open_path", { path }).catch((error) =>
              message(String(error), { title: "打开结果失败", kind: "error" }),
            );
          }}
        >
          打开 {basename(path)}
        </Button>
      ))}
      {!!missing.length && (
        <span role="status">缺少场站：{missing.join("、")}</span>
      )}
    </section>
  );
}

function LogConsole({
  tool,
  text,
  status,
  busy,
  cancel,
  cancellable,
  version,
}: {
  tool: Tool;
  text: string;
  status: string;
  busy: boolean;
  cancel: () => void;
  cancellable: boolean;
  version: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [query, setQuery] = useState("");
  const [copied, setCopied] = useState(false);
  const pre = useRef<HTMLPreElement>(null);
  const last = useRef("");
  useEffect(() => {
    if (
      pre.current &&
      pre.current.scrollTop + pre.current.clientHeight >=
        pre.current.scrollHeight - 100
    )
      pre.current.scrollTop = pre.current.scrollHeight;
  }, [text]);
  const header = `模块：${tool.name}  版本：${version}  开始时间：${new Date().toLocaleString("sv-SE")}\n`;
  if (!last.current) last.current = header;
  const contents = last.current + text;
  return (
    <div
      className={`task-console ${tool.id === "wps-writer" ? "wps-log" : ""}`}
    >
      {tool.id !== "wps-writer" && (
        <>
          <div className="cancel-line">
            {busy && <progress aria-label="任务进度" />}
            <Button disabled={!cancellable} run={cancel}>
              取消任务
            </Button>
          </div>
          <div
            id={`${tool.id}-status`}
            className="module-status"
            role="status"
            aria-live="polite"
          >
            {status}
          </div>
        </>
      )}
      <div className="log-toolbar">
        <span>运行日志</span>
        <span className="toolbar-spacer" />
        <Button
          primary
          run={() => {
            const errors = contents
              .split("\n")
              .filter((line) =>
                /错误|失败|Error|Exception|Traceback|建议/.test(line),
              );
            void copyText(errors.join("\n") || contents).then(() => {
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            });
          }}
        >
          {copied ? "已复制" : "复制错误"}
        </Button>
        <Button run={() => setExpanded(true)}>展开</Button>
      </div>
      <pre
        ref={pre}
        aria-label={`${tool.name}运行日志`}
        tabIndex={0}
        className="log-text"
      >
        {contents}
      </pre>
      {expanded && (
        <Modal title="运行日志" wide onClose={() => setExpanded(false)}>
          <div className="table-toolbar">
            <label>
              查找{" "}
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            <Button
              run={() => {
                const matches = contents
                  .split("\n")
                  .filter((line) => line.includes(query));
                void message(matches.join("\n") || "未找到匹配内容", {
                  title: "查找日志",
                });
              }}
            >
              查找
            </Button>
            <Button
              run={() => {
                void copyText(contents);
              }}
            >
              复制全部
            </Button>
            <Button
              run={() => {
                void call<string>("ui.logs.save", {
                  tool_id: tool.id,
                  text: contents,
                }).then((path) =>
                  message(`已保存：${path}`, { title: "保存日志" }),
                );
              }}
            >
              保存日志
            </Button>
          </div>
          <pre className="expanded-log" tabIndex={0}>
            {contents}
          </pre>
        </Modal>
      )}
    </div>
  );
}

export function ToolboxApp({
  sample = false,
  hostError = "",
}: {
  sample?: boolean;
  hostError?: string;
}) {
  const [boot, setBoot] = useState<Bootstrap | null>(null);
  const [current, setCurrent] = useState(sample ? "online-energy" : "wps-writer");
  const [forms, setForms] = useState<Record<string, Values>>({});
  const formsRef = useRef(forms);
  formsRef.current = forms;
  const [tasks, setTasks] = useState<Task[]>([]);
  const tasksRef = useRef(tasks);
  tasksRef.current = tasks;
  const [logs, setLogs] = useState<Record<string, string>>({});
  const [statuses, setStatuses] = useState<Record<string, string>>({});
  const [pending, setPending] = useState<Record<string, boolean>>({});
  const [rail, setRail] = useState(true);
  const [selectedTask, setSelectedTask] = useState("");
  const wpsSave = useRef(Promise.resolve());
  const [wps, setWps] = useState<Values | null>(null);
  const [editorEpoch, setEditorEpoch] = useState(0);
  const [manual, setManual] = useState(false);
  const [updatePanel, setUpdatePanel] = useState<any>(null);
  const [error, setError] = useState("");
  const [navigation, setNavigation] = useState("暂无记录");
  const page = useRef<HTMLDivElement>(null);
  const flush = useRef<(required?: boolean) => Promise<boolean>>(
    async () => true,
  );
  const cursor = useRef(0);
  const requests = useRef(new Map<string, { signature: string; id: string }>());
  const ended = useRef(new Set<string>());
  const changing = useRef(false);
  const refreshFlight = useRef<Promise<Task[]> | null>(null);
  const updateJob = useRef<string | null>(null);
  const append = (id: string, text: string) =>
    setLogs((previous) => ({
      ...previous,
      [id]: (previous[id] ?? "") + text + "\n",
    }));
  useEffect(() => {
    void Promise.all([
      call<Bootstrap>("ui.bootstrap"),
      call<Task[]>("tasks"),
      call<Values>("wps.get"),
      call<EventBatch>("events", { after: 0 }),
    ])
      .then(([data, history, config, batch]) => {
        setBoot(data);
        setForms(
          Object.fromEntries(
            data.catalog.map((tool) => [
              tool.id,
              {
                ...Object.fromEntries(
                  tool.fields.map((field) => [field.key, field.default]),
                ),
                files: [],
                _selected_files: [],
                ...data.initial?.[tool.id],
              },
            ]),
          ),
        );
        const restoredDayReport = data.initial?.["market-report"]?.day_report;
        if (restoredDayReport)
          void call<string>("ui.market.report_date", {
            path: restoredDayReport,
          })
            .then((day) =>
              setForms((previous) => {
                const report = previous["market-report"];
                if (
                  report.day_report !== restoredDayReport ||
                  report._report_date
                )
                  return previous;
                return {
                  ...previous,
                  "market-report": { ...report, _report_date: day },
                };
              }),
            )
            .catch(() => {}); // The report action can show a file-specific error.
        setTasks(history);
        history.filter(terminal).forEach((task) => ended.current.add(task.id));
        setWps(config);
        cursor.current = batch.cursor;
        setLogs({
          "online-energy": `[${stamp()}] 请选择日期范围后开始导出。\n`,
          ...restoreTaskLogs(history, batch),
        });
      })
      .catch((error) => setError(String(error)));
  }, []);
  const refresh = useCallback(async () => {
    if (refreshFlight.current) return refreshFlight.current;
    const work = (async () => {
      const [history, batch] = await Promise.all([
        call<Task[]>("tasks"),
        call<any>("events", { after: cursor.current }),
      ]);
      cursor.current = batch.cursor;
      setTasks(history);
      for (const event of batch.events) {
        const task = history.find((item) => item.id === event.task_id);
        if (task && event.kind === "log") append(task.tool_id, event.message);
        if (event.task_id === updateJob.current && event.kind === "progress")
          setUpdatePanel((previous: any) => ({
            ...previous,
            progress: event.progress,
          }));
      }
      for (const task of history) {
        if (terminal(task) && !ended.current.has(task.id)) {
          ended.current.add(task.id);
          requests.current.delete(task.tool_id);
          setStatuses((previous) => ({
            ...previous,
            [task.tool_id]:
              task.state === "succeeded"
                ? "已完成"
                : task.state === "cancelled"
                  ? "已取消"
                  : "执行失败",
          }));
          append(
            task.tool_id,
            `${task.result?.message ?? taskLabels[task.state]}${task.error ? `：${task.error}` : ""}`,
          );
          if (task.state === "failed")
            void message(task.error ?? "任务执行失败", {
              title: "执行失败",
              kind: "error",
            });
        }
      }
      return history;
    })();
    refreshFlight.current = work;
    try {
      return await work;
    } finally {
      refreshFlight.current = null;
    }
  }, []);
  const running = tasks.some((task) => !terminal(task));
  useEffect(() => {
    if (!running) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        await refresh();
      } catch (error) {
        setError(`后台状态读取失败：${String(error)}`);
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
  }, [running, refresh]);
  async function execute(
    toolId: string,
    action = "run",
    override: Values = {},
    wait = false,
  ): Promise<any> {
    const tool = boot!.catalog.find((tool) => tool.id === toolId)!;
    const values = { ...formsRef.current[toolId], ...override };
    if (toolId === current && page.current) {
      page.current
        .querySelectorAll<HTMLInputElement | HTMLSelectElement>("[data-field]")
        .forEach((input) => {
          const key = input.dataset.field!;
          if (input instanceof HTMLInputElement && input.type === "checkbox")
            values[key] = input.checked;
          else if (
            input instanceof HTMLInputElement &&
            input.type === "radio"
          ) {
            if (input.checked) values[key] = input.value;
          } else values[key] = input.value;
        });
    }
    const accepted = Object.fromEntries(
      tool.fields.map((field) => [
        field.key,
        values[field.key] ?? field.default,
      ]),
    );
    Object.assign(accepted, override);
    if (
      (await call<boolean>("ui.requires_credentials", {
        tool_id: toolId,
        action,
        values: accepted,
      })) &&
      !(await flush.current(true))
    )
      throw new Error("账号尚未保存，请完成右侧账号输入。");
    setPending((previous) => ({ ...previous, [toolId]: true }));
    try {
      const prepared = await call<any>("prepare_task", {
        tool_id: toolId,
        action,
        values: accepted,
      });
      if (
        prepared.confirmation &&
        !(await ask(prepared.confirmation, {
          title: "确认操作",
          kind: "warning",
        }))
      )
        return null;
      const signature = JSON.stringify([action, prepared.values]);
      if (requests.current.get(toolId)?.signature !== signature)
        requests.current.set(toolId, { signature, id: crypto.randomUUID() });
      const taskId = await call<string>("start", {
        tool_id: toolId,
        action,
        values: prepared.values,
        request_id: requests.current.get(toolId)!.id,
        confirmed: true,
      });
      setForms((previous) => ({
        ...previous,
        [toolId]: { ...previous[toolId], ...prepared.values },
      }));
      setStatuses((previous) => ({ ...previous, [toolId]: "正在执行…" }));
      append(
        toolId,
        `[${stamp()}] ${tool.actions.find((item) => item.id === action)?.label ?? action}`,
      );
      await refresh();
      if (wait) {
        while (true) {
          const history = await call<Task[]>("tasks");
          const task = history.find((item) => item.id === taskId);
          if (task && terminal(task)) {
            await refresh();
            if (task.state !== "succeeded")
              throw new Error(task.error ?? "任务已取消");
            return task.result;
          }
          await new Promise((resolve) => setTimeout(resolve, 200));
        }
      }
      return taskId;
    } finally {
      setPending((previous) => ({ ...previous, [toolId]: false }));
    }
  }
  async function run(action = "run") {
    try {
      await execute(current, action);
    } catch (error) {
      await message(String(error), { title: "无法执行", kind: "error" });
    }
  }
  async function cancel(id: string) {
    await call("cancel", { task_id: id });
    await refresh();
  }
  function navigate(id: string) {
    const patch: Values = {};
    page.current
      ?.querySelectorAll<HTMLInputElement | HTMLSelectElement>("[data-field]")
      .forEach((input) => {
        const key = input.dataset.field!;
        if (input instanceof HTMLInputElement && input.type === "checkbox")
          patch[key] = input.checked;
        else if (input instanceof HTMLInputElement && input.type === "radio") {
          if (input.checked) patch[key] = input.value;
        } else patch[key] = input.value;
      });
    setForms((previous) => ({
      ...previous,
      [current]: { ...previous[current], ...patch },
    }));
    const start = performance.now();
    setCurrent(id);
    setNavigation(`${Math.round(performance.now() - start)} ms`);
  }
  async function close(restart = false) {
    if (changing.current) return;
    changing.current = true;
    try {
      if (!(await flush.current())) return;
      await wpsSave.current;
      const history = await call<Task[]>("tasks");
      const active = history.filter((task) => !terminal(task));
      if (active.some((task) => !task.cancellable)) {
        await message("仍有不可安全中断的写入任务，请等待完成。", {
          title: "任务尚未结束",
          kind: "warning",
        });
        return;
      }
      if (
        active.length &&
        !(await ask(`仍有 ${active.length} 个任务运行，是否取消任务并退出？`, {
          title: "确认退出",
          kind: "warning",
        }))
      )
        return;
      const editor = await call<any>("ui.section.status");
      if (
        editor.dirty &&
        !(await ask("浏览器编辑仍有未应用的修改，确认退出？", {
          title: "确认退出",
        }))
      )
        return;
      await invoke("webview_close", { cancel: !!active.length, restart });
    } catch (error) {
      await message(String(error), { title: "无法退出", kind: "error" });
    } finally {
      changing.current = false;
    }
  }
  const closeRef = useRef(close);
  closeRef.current = close;
  useEffect(() => {
    const unlisten = listen("toolbox-close-requested", () => {
      void closeRef.current();
    });
    return () => {
      void unlisten.then((dispose) => dispose());
    };
  }, []);
  useEffect(() => {
    if (current !== "section-analysis") return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      const state = await call<any>("ui.section.status").catch((error) => {
        if (!stopped) setError(`接线图编辑状态读取失败：${String(error)}`);
        return { closed: true };
      });
      if (stopped) return;
      if (state.applied) {
        setForms((previous) => ({
          ...previous,
          "section-analysis": {
            ...previous["section-analysis"],
            topology_path: state.applied,
          },
        }));
        append(
          "section-analysis",
          "浏览器编辑的拓扑已应用，后续分析使用该快照。",
        );
      }
      if (!stopped && !state.closed)
        timer = setTimeout(() => {
          void poll();
        }, 250);
    }
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [current, editorEpoch]);
  async function checkUpdates() {
    try {
      setUpdatePanel({ checking: true });
      await call("updates.check");
      while (true) {
        const status = await call<any>("updates.status");
        if (!status.checking) {
          setUpdatePanel(status);
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 300));
      }
    } catch (error) {
      setUpdatePanel({ error: String(error) });
    }
  }
  async function waitUpdateTask(id: string) {
    updateJob.current = id;
    while (true) {
      const history = await refresh();
      const task = history.find((item) => item.id === id);
      if (task && terminal(task)) {
        if (task.state !== "succeeded")
          throw new Error(
            task.state === "cancelled"
              ? "已取消下载"
              : (task.error ?? "更新失败"),
          );
        return task.result;
      }
      await new Promise((resolve) => setTimeout(resolve, 150));
    }
  }
  async function performUpdate() {
    const release = updatePanel?.release;
    if (!release || !release.asset || !(await flush.current())) return;
    try {
      await wpsSave.current;
      await call("ui.credentials.cancel");
      await call("updates.reserve");
      setUpdatePanel({
        release,
        downloading: true,
        progress: 0,
        status: "正在连接 GitHub…",
      });
      const download = await call<string>("updates.download", {
        version: release.version,
      });
      const result = await waitUpdateTask(download);
      setUpdatePanel({
        release,
        installing: true,
        progress: 1,
        status: "正在校验镜像和应用，准备自动安装…",
      });
      const install = await call<string>("updates.prepare_install", {
        version: release.version,
        path: result.outputs[0],
      });
      await waitUpdateTask(install);
      await call("updates.commit_install");
      await invoke("webview_close", { cancel: false });
    } catch (error) {
      try {
        await call("updates.abort");
      } catch {}
      setUpdatePanel({ release, error: String(error), status: String(error) });
    } finally {
      updateJob.current = null;
    }
  }
  function dismissUpdate() {
    if (updatePanel?.installing) return;
    if (updatePanel?.downloading && updateJob.current) {
      void call("cancel", { task_id: updateJob.current });
      setUpdatePanel((previous: any) => ({
        ...previous,
        status: "正在取消下载…",
      }));
    } else setUpdatePanel(null);
  }
  if (!boot)
    return (
      <main className="tb-loading" role={error ? "alert" : "status"}>
        {error || "正在加载电力工具箱…"}
      </main>
    );
  const tool = boot.catalog.find((tool) => tool.id === current)!;
  const values = forms[current] ?? {};
  const active = tasks.find(
    (task) => task.tool_id === current && !terminal(task),
  );
  const busy =
    !!active ||
    !!pending[current] ||
    !!updatePanel?.downloading ||
    !!updatePanel?.installing;
  const update = (patch: Values) =>
    setForms((previous) => ({
      ...previous,
      [current]: { ...previous[current], ...patch },
    }));
  async function openOutput() {
    const selected = { ...values };
    page.current
      ?.querySelectorAll<HTMLInputElement>("input[data-field]")
      .forEach((input) => {
        const key = input.dataset.field!;
        if (input.type === "checkbox") selected[key] = input.checked;
        else if (input.type !== "radio" || input.checked)
          selected[key] = input.value;
      });
    try {
      const path = await call<string>("ui.output.directory", {
        tool_id: current,
        values: selected,
      });
      await invoke("open_path", { path });
    } catch (error) {
      await message(String(error), { title: "打开目录失败", kind: "error" });
    }
  }
  async function autoMatch() {
    try {
      update(
        await call("auto_match", {
          tool_id: current,
          report_date:
            document.querySelector<HTMLInputElement>(
              '[data-field="report_date"]',
            )?.value ?? values.report_date,
        }),
      );
    } catch (error) {
      await message(String(error), { title: "自动匹配失败", kind: "error" });
    }
  }
  return (
    <div className={`toolbox-app ${rail ? "with-rail" : ""}`}>
      <aside className="toolbox-sidebar">
        <h1>电力工作台</h1>
        <div className="update-space">
          {updatePanel?.release && (
            <Button
              className="new-update"
              run={() => {
                void checkUpdates();
              }}
            >
              new
            </Button>
          )}
        </div>
        <nav aria-label="工具模块">
          {[...new Set(boot.catalog.map((tool) => tool.category))].map(
            (category) => (
              <section key={category}>
                <h2>{category}</h2>
                {boot.catalog
                  .filter((tool) => tool.category === category)
                  .map((tool) => (
                    <button
                      key={tool.id}
                      id={`nav-${tool.id}`}
                      className={current === tool.id ? "selected" : ""}
                      aria-current={current === tool.id ? "page" : undefined}
                      onClick={() => navigate(tool.id)}
                    >
                      {tool.name}
                    </button>
                  ))}
              </section>
            ),
          )}
        </nav>
      </aside>
      <main
        ref={page}
        className={`tool-page ${current === "wps-writer" ? "wps-page" : ""}`}
        data-tool={current}
        id="tool-page"
      >
        {(hostError || error) && (
          <p className="error" role="alert">
            {hostError || error}
          </p>
        )}
        <h1>{current === "wps-writer" ? "WPS 写入工具" : tool.name}</h1>
        {current === "wps-writer" ? (
          wps && (
            <WpsEditor
              config={wps}
              onConfig={async (config) => {
                setWps(config);
                const pending = wpsSave.current
                  .catch(() => {})
                  .then(() => call<void>("wps.save", { config }));
                wpsSave.current = pending;
                try {
                  await pending;
                } catch (error) {
                  await message(String(error), {
                    title: "配置保存失败",
                    kind: "error",
                  });
                }
              }}
              execute={(action, values) =>
                execute("wps-writer", action, values, true)
              }
            />
          )
        ) : (
          <PageForms
            tool={tool}
            values={values}
            setValues={update}
            busy={busy}
            run={(action) => {
              void run(action);
            }}
            openOutput={() => {
              void openOutput();
            }}
            autoMatch={() => {
              void autoMatch();
            }}
            editor={() => {
              void call("ui.section.editor", {
                input_path: values.files?.length === 1 ? values.files[0] : null,
              })
                .then(() => setEditorEpoch((epoch) => epoch + 1))
                .catch((error) =>
                  message(String(error), {
                    title: "浏览器编辑",
                    kind: "error",
                  }),
                );
            }}
            manual={() => {
              const path =
                page.current?.querySelector<HTMLInputElement>(
                  '[data-field="day_report"]',
                )?.value ?? values.day_report;
              void call<string>("ui.market.report_date", { path })
                .then((day) => {
                  update({
                    day_report: path,
                    _report_date: day,
                    ...(day !== values._report_date
                      ? { manual_market: "" }
                      : {}),
                  });
                  setManual(true);
                })
                .catch((error) =>
                  message(String(error), {
                    title: "报告日期识别失败",
                    kind: "error",
                  }),
                );
            }}
            reportDate={(path) => {
              void call<string>("ui.market.report_date", { path })
                .then((day) =>
                  update({
                    _report_date: day,
                    ...(day !== values._report_date
                      ? { manual_market: "" }
                      : {}),
                  }),
                )
                .catch((error) =>
                  message(String(error), {
                    title: "报告日期识别失败",
                    kind: "error",
                  }),
                );
            }}
          />
        )}
        {current !== "report" && (
          <TaskResults
            task={tasks
              .filter((task) => task.tool_id === current && terminal(task))
              .at(-1)}
          />
        )}
        <LogConsole
          key={current}
          tool={tool}
          version={boot.version}
          text={logs[current] ?? ""}
          status={statuses[current] ?? ""}
          busy={busy}
          cancellable={!!active?.cancellable && active.state !== "cancelling"}
          cancel={() => {
            if (active) void cancel(active.id);
          }}
        />
      </main>
      {rail && (
        <aside className="toolbox-rail">
          <section className="task-center">
            <h2>任务中心</h2>
            <p className="muted small">最近的运行记录</p>
            <div
              className="task-list"
              role="listbox"
              aria-label="最近的运行记录"
            >
              {!tasks.length ? (
                <span className="empty-records">暂无运行记录</span>
              ) : (
                tasks.slice(-12).map((task) => (
                  <button
                    key={task.id}
                    role="option"
                    aria-selected={selectedTask === task.id}
                    className={`${selectedTask === task.id ? "selected" : ""} ${task.state}`}
                    onClick={() => setSelectedTask(task.id)}
                  >
                    {taskLabels[task.state]} · {task.name}
                  </button>
                ))
              )}
            </div>
            <Button
              className="quiet"
              disabled={
                !tasks.find(
                  (task) =>
                    task.id === selectedTask &&
                    !terminal(task) &&
                    task.cancellable,
                )
              }
              run={() => {
                void cancel(selectedTask);
              }}
            >
              取消选中任务
            </Button>
          </section>
          <Credentials
            sample={sample}
            loginAvailable={
              current !== "wps-writer" &&
              current !== "section-analysis" &&
              tool.actions.some((item) => item.id === "login")
            }
            loginTask={tasks.filter((task) => task.tool_id === current).at(-1)}
            register={(handler) => {
              flush.current = handler;
            }}
            login={() => {
              void run("login");
            }}
          />
          <Diagnostics navigation={navigation} />
        </aside>
      )}
      <footer className="toolbox-status">
        <span>当前功能：{tool.name}</span>
        <span className="toolbar-spacer" />
        <span className="sample-indicator">
          {sample ? "离线样例" : "真实业务模式"}
        </span>
        <Button className="quiet" run={() => setRail(!rail)}>
          {rail ? "收起辅助面板" : "展开辅助面板"}
        </Button>
        <button
          className="version"
          onClick={() => {
            void checkUpdates();
          }}
        >
          版本 v{boot.version}
        </button>
        <Button
          className="quiet"
          run={() => {
            void close(true);
          }}
        >
          重启源码版
        </Button>
      </footer>
      {manual && (
        <ManualMarket
          reportDate={values._report_date}
          initial={values.manual_market ? JSON.parse(values.manual_market) : {}}
          onClose={() => setManual(false)}
          onSave={(data) => {
            update({
              manual_market: Object.keys(data).length
                ? JSON.stringify(data)
                : "",
            });
            setManual(false);
          }}
        />
      )}
      {updatePanel && (
        <Modal
          title={updatePanel.release ? "软件更新" : "检查更新"}
          onClose={dismissUpdate}
        >
          {updatePanel.checking ? (
            <p>正在检查更新…</p>
          ) : updatePanel.error && !updatePanel.release ? (
            <p role="alert">{updatePanel.error}</p>
          ) : updatePanel.release ? (
            <>
              <h3>发现新版本 {updatePanel.release.version}</h3>
              <p>
                当前 v{boot.version} → {updatePanel.release.version} · Apple
                芯片
              </p>
              <pre className="release-notes">
                {updatePanel.release.notes ?? updatePanel.release.body ?? ""}
              </pre>
              <progress
                aria-label="更新进度"
                max={1}
                value={updatePanel.progress ?? 0}
              />
              <p role="status">
                {updatePanel.status ??
                  "开始更新后将自动下载、校验、退出并安装，再重新启动工具箱；配置和数据保留。日常优先使用增量补丁。"}
              </p>
              <footer className="update-actions">
                <Button
                  run={() => {
                    void call("ui.updates.page", {
                      url: updatePanel.release.page,
                    });
                  }}
                >
                  发布页面
                </Button>
                <span className="toolbar-spacer" />
                <Button disabled={!!updatePanel.installing} run={dismissUpdate}>
                  {updatePanel.downloading ? "取消下载" : "稍后"}
                </Button>
                <Button
                  primary
                  disabled={
                    !!updatePanel.downloading ||
                    !!updatePanel.installing ||
                    !updatePanel.release.asset
                  }
                  run={() => {
                    void performUpdate();
                  }}
                >
                  {updatePanel.installing
                    ? "安装准备中…"
                    : updatePanel.downloading
                      ? "下载中…"
                      : updatePanel.error
                        ? "重试下载"
                        : "开始更新"}
                </Button>
              </footer>
            </>
          ) : (
            <p>当前已是最新版本。</p>
          )}
        </Modal>
      )}
    </div>
  );
}
