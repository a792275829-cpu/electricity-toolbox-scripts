import { useEffect, useRef, useState } from "react";
import { ask, message } from "@tauri-apps/plugin-dialog";
import { call } from "../api";
import { Button, Check, Frame, Modal, Segments, choosePath } from "./controls";
import { TextField } from "./PageForms";
import type { Values } from "./types";

const regionFields = [
  ["source_sheet", "来源工作表"],
  ["source_start", "来源起始单元格"],
  ["source_end", "来源结束单元格"],
  ["target_sheet", "目标工作表"],
  ["target_start", "目标起始单元格"],
  ["target_end", "目标结束单元格"],
];
const blankRegion = () =>
  Object.fromEntries(regionFields.map(([key]) => [key, ""]));
const copy = <T,>(value: T): T => structuredClone(value);
function dialogValues(base: Values): Values {
  const value = { ...base };
  const dialog = Array.from(
    document.querySelectorAll<HTMLDialogElement>("dialog[open]"),
  ).at(-1);
  dialog
    ?.querySelectorAll<HTMLInputElement>("input[data-field]")
    .forEach((input) => {
      value[input.dataset.field!] = input.value;
    });
  return value;
}

export function DataTable({
  headings,
  rows,
  selected,
  onSelect,
  onEdit,
  name = "配置",
}: {
  headings: string[];
  rows: any[][];
  selected: number[];
  onSelect: (rows: number[]) => void;
  onEdit?: () => void;
  name?: string;
}) {
  return (
    <div className="tree-scroll">
      <table className="tb-tree">
        <thead>
          <tr>
            {headings.map((heading) => (
              <th key={heading}>{heading}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr
              key={index}
              id={`table-${name}-${index}`}
              aria-label={`${name} ${index + 1}：${row[0]}`}
              tabIndex={0}
              aria-selected={selected.includes(index)}
              className={selected.includes(index) ? "selected" : ""}
              onClick={(event) =>
                onSelect(
                  event.metaKey || event.ctrlKey
                    ? selected.includes(index)
                      ? selected.filter((i) => i !== index)
                      : [...selected, index]
                    : [index],
                )
              }
              onDoubleClick={onEdit}
              onKeyDown={(event) => {
                if (event.key === " " || event.key === "Enter") {
                  event.preventDefault();
                  onSelect([index]);
                  if (event.key === "Enter") onEdit?.();
                }
                if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                  event.preventDefault();
                  const next = Math.max(
                    0,
                    Math.min(
                      rows.length - 1,
                      index + (event.key === "ArrowDown" ? 1 : -1),
                    ),
                  );
                  onSelect([next]);
                  (
                    event.currentTarget.parentElement?.children[
                      next
                    ] as HTMLElement
                  )?.focus();
                }
              }}
            >
              {row.map((value, n) => (
                <td key={n} title={String(value ?? "")}>
                  {String(value ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RegionEditor({
  initial,
  sourceSheets,
  targetSheets,
  onSave,
  onClose,
}: {
  initial: Values;
  sourceSheets: string[];
  targetSheets: string[];
  onSave: (region: Values) => void;
  onClose: () => void;
}) {
  const [value, setValue] = useState(copy(initial));
  const [error, setError] = useState("");
  return (
    <Modal title="写入区域" onClose={onClose}>
      <div className="dialog-form">
        {regionFields.map(([key, label]) => (
          <div className="form-row" key={key}>
            <label htmlFor={`field-${key}`}>{label}</label>
            <TextField
              field={key}
              value={value[key]}
              label={label}
              onChange={(text) => setValue({ ...value, [key]: text })}
            />
            {key.endsWith("sheet") && (
              <select
                aria-label={`${label}列表`}
                value=""
                onChange={(event) =>
                  setValue({ ...value, [key]: event.target.value })
                }
              >
                <option value="">选择工作表</option>
                {(key === "source_sheet" ? sourceSheets : targetSheets).map(
                  (sheet) => (
                    <option key={sheet}>{sheet}</option>
                  ),
                )}
              </select>
            )}
          </div>
        ))}
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <footer>
        <Button
          run={() => {
            const current = dialogValues(value);
            if (regionFields.some(([key]) => !String(current[key]).trim())) {
              setError("请填写来源和目标的工作表、起始单元格及结束单元格。");
              return;
            }
            onSave(current);
          }}
        >
          确定
        </Button>
        <Button run={onClose}>取消</Button>
      </footer>
    </Modal>
  );
}

function DocumentEditor({
  initial,
  config,
  save,
  onClose,
  execute,
}: {
  initial: Values;
  config: Values;
  save: (draft: Values, close: boolean) => void;
  onClose: () => void;
  execute: (action: string, values: Values) => Promise<any>;
}) {
  const [draft, setDraft] = useState<Values>(() => ({
    ...copy(initial),
    source_doc_title:
      config.document_titles?.[initial.source_url] ??
      initial.source_doc_title ??
      "",
    target_doc_title:
      config.document_titles?.[initial.kdocs_url] ??
      initial.target_doc_title ??
      "",
  }));
  const [selected, setSelected] = useState<number[]>([]);
  const [region, setRegion] = useState<{ index: number; value: Values } | null>(
    null,
  );
  const [sourceSheets, setSourceSheets] = useState<string[]>([]);
  const [targetSheets, setTargetSheets] = useState<string[]>([]);
  const [status, setStatus] = useState("");
  const [reading, setReading] = useState(false);
  const revision = useRef(0);
  const latest = useRef(draft);
  latest.current = draft;
  const valid = (value: Values) =>
    value.kdocs_url &&
    value.regions.length &&
    (value.source_type === "kdocs" ? value.source_url : value.local_file);
  useEffect(() => {
    if (!valid(draft)) return;
    const timer = setTimeout(() => save(draft, false), 500);
    return () => clearTimeout(timer);
  }, [draft]);
  useEffect(() => {
    revision.current++;
    setSourceSheets([]);
    setReading(false);
  }, [draft.source_url, draft.local_file, draft.source_type]);
  async function read(target = false) {
    const token = ++revision.current;
    setReading(true);
    setStatus("正在后台读取表格，您可以继续编辑配置…");
    const candidate = dialogValues(copy(latest.current));
    latest.current = candidate;
    setDraft(candidate);
    try {
      if (!target && candidate.source_type === "excel") {
        const sheets = await call<string[]>("ui.local.sheets", {
          path: candidate.local_file,
        });
        if (token === revision.current) setSourceSheets(sheets);
      } else {
        const url = target ? candidate.kdocs_url : candidate.source_url;
        const info = await execute("inspect", {
          config: JSON.stringify(config),
          operation: JSON.stringify({
            method: "read_online_document_info",
            arguments: {
              url,
              browser_mode: config.browser_mode,
              cdp_url: config.cdp_url,
            },
          }),
        });
        if (
          token !== revision.current ||
          (target ? latest.current.kdocs_url : latest.current.source_url) !==
            url
        )
          return;
        (target ? setTargetSheets : setSourceSheets)(info.sheets ?? []);
        setDraft((current) => ({
          ...current,
          [target ? "target_doc_title" : "source_doc_title"]: info.title ?? "",
        }));
      }
      if (token === revision.current) setStatus("工作表读取完成。");
    } catch (error) {
      setStatus(`读取失败，已保留原有工作表配置。${String(error)}`);
    } finally {
      if (token === revision.current) setReading(false);
    }
  }
  const set = (key: string, value: any) =>
    setDraft((current) => ({ ...current, [key]: value }));
  const row = (key: string, label: string, button?: string, target = false) => (
    <div className="form-row" key={key}>
      <label>{label}</label>
      <TextField
        field={key}
        label={label}
        value={draft[key]}
        readOnly={key === "source_doc_title"}
        list={key.endsWith("url") ? "wps-url-history" : undefined}
        onChange={(value) => {
          set(key, value);
          if (key === "kdocs_url") {
            revision.current++;
            setTargetSheets([]);
          }
        }}
      />
      {button && (
        <Button
          disabled={reading}
          run={() => {
            void read(target);
          }}
        >
          {button}
        </Button>
      )}
    </div>
  );
  return (
    <Modal
      title={initial.name ? "编辑配置" : "添加配置"}
      wide
      onClose={onClose}
    >
      <datalist id="wps-url-history">
        {(config.recent_urls ?? []).map((url: string) => (
          <option key={url} value={url}>
            {config.document_titles?.[url] ?? url}
          </option>
        ))}
      </datalist>
      <Frame title="来源与目标">
        <div className="dialog-form">
          {row("name", "配置名称")}
          <div className="form-row">
            <label>数据来源</label>
            <Segments
              choices={[
                ["excel", "本地表格"],
                ["kdocs", "金山文档"],
              ]}
              value={draft.source_type}
              onChange={(value) => set("source_type", value)}
            />
          </div>
          {row("source_url", "来源表格链接", "刷新来源工作表")}
          {row("kdocs_url", "目标表格链接", "刷新目标工作表", true)}
          {row("source_doc_title", "来源表格名称")}
          <div className="form-row">
            <label>本地文件</label>
            <TextField
              field="local_file"
              label="本地文件"
              value={draft.local_file}
              onChange={(value) => set("local_file", value)}
            />
            <Button
              icon
              run={() => {
                void choosePath("wps.local_file", draft.local_file).then(
                  (value) => {
                    if (value) set("local_file", String(value));
                  },
                );
              }}
            >
              选择文件
            </Button>
            <Button
              run={() => {
                void read();
              }}
            >
              刷新工作表
            </Button>
          </div>
        </div>
        <p className="muted" role="status">
          {status}
        </p>
      </Frame>
      <Frame title="写入区域" className="region-frame">
        <div className="table-toolbar">
          <Button
            run={() =>
              setRegion({
                index: -1,
                value: {
                  ...blankRegion(),
                  source_sheet: sourceSheets[0] ?? "",
                  target_sheet: targetSheets[0] ?? "",
                },
              })
            }
          >
            添加区域
          </Button>
          <Button
            run={() => {
              if (selected.length)
                setRegion({
                  index: selected[0],
                  value: draft.regions[selected[0]],
                });
            }}
          >
            编辑
          </Button>
          <Button
            run={() => {
              if (selected.length)
                set("regions", [
                  ...draft.regions,
                  copy(draft.regions[selected[0]]),
                ]);
            }}
          >
            复制
          </Button>
          <Button
            run={() =>
              set(
                "regions",
                draft.regions.filter(
                  (_: any, n: number) => !selected.includes(n),
                ),
              )
            }
          >
            删除
          </Button>
        </div>
        <DataTable
          name="区域"
          headings={regionFields.map(([, label]) => label)}
          rows={draft.regions.map((region: Values) =>
            regionFields.map(([key]) => region[key]),
          )}
          selected={selected}
          onSelect={setSelected}
          onEdit={() => {
            if (selected.length)
              setRegion({
                index: selected[0],
                value: draft.regions[selected[0]],
              });
          }}
        />
      </Frame>
      <footer>
        <Button run={onClose}>取消</Button>
        <Button
          run={() => {
            const current = dialogValues(draft);
            if (!valid(current)) {
              setStatus(
                "请填写目标表格链接、数据来源，并至少添加一个写入区域。",
              );
              return;
            }
            save(current, true);
          }}
        >
          保存配置
        </Button>
      </footer>
      {region && (
        <RegionEditor
          initial={region.value}
          sourceSheets={sourceSheets}
          targetSheets={targetSheets}
          onClose={() => setRegion(null)}
          onSave={(value) => {
            const rows = [...draft.regions];
            if (region.index < 0) rows.push(value);
            else rows[region.index] = value;
            set("regions", rows);
            setRegion(null);
          }}
        />
      )}
    </Modal>
  );
}

export function WpsEditor({
  config,
  onConfig,
  execute,
}: {
  config: Values;
  onConfig: (value: Values) => Promise<void>;
  execute: (action: string, values: Values) => Promise<any>;
}) {
  const [selected, setSelected] = useState<number[]>([]);
  const [editing, setEditing] = useState<{
    index: number;
    draft: Values;
  } | null>(null);
  const [browserStatus, setBrowserStatus] = useState("尚未检查浏览器连接");
  const [login, setLogin] = useState("尚未检查");
  const [preview, setPreview] = useState<any>(null);
  const configRef = useRef(config);
  configRef.current = config;
  const editIndex = useRef(-1);
  const configs: Values[] = config.configs ?? [];
  const persist = (patch: Values) => {
    const value = { ...configRef.current, ...patch };
    configRef.current = value;
    return onConfig(value);
  };
  async function status(action: string) {
    try {
      const result = await execute(action, {
        config: JSON.stringify(configRef.current),
        operation: JSON.stringify({}),
        selected_indexes: "1",
      });
      setBrowserStatus("浏览器已连接");
      setLogin(result.message ?? "检查完成");
    } catch (error) {
      setBrowserStatus("浏览器连接失败");
      setLogin(String(error));
    }
  }
  async function apply(previewOnly = false) {
    if (!selected.length) {
      await message("请先选择配置，再预览或写入。", {
        title: "未选择配置",
        kind: "warning",
      });
      return;
    }
    if (
      !previewOnly &&
      !(await ask("确认将所选配置的数据写入目标表格？写入完成后会回读核对。", {
        title: "确认写入",
        kind: "warning",
      }))
    )
      return;
    try {
      const result = await execute(previewOnly ? "preview" : "run", {
        config: JSON.stringify(config),
        selected_indexes: selected.map((index) => index + 1).join(","),
        operation: "{}",
      });
      if (previewOnly) setPreview(result);
    } catch (error) {
      await message(String(error), { title: "执行失败", kind: "error" });
    }
  }
  function edit(index: number) {
    editIndex.current = index;
    setEditing({
      index,
      draft:
        index < 0
          ? {
              name: "",
              source_type: "excel",
              source_url: "",
              local_file: "",
              kdocs_url: "",
              regions: [],
            }
          : copy(configs[index]),
    });
  }
  async function move(delta: number) {
    if (selected.length !== 1) return;
    const index = selected[0];
    const next = index + delta;
    if (next < 0 || next >= configs.length) return;
    const rows = [...configs];
    [rows[index], rows[next]] = [rows[next], rows[index]];
    await persist({ configs: rows });
    setSelected([next]);
  }
  return (
    <div className="wps-body">
      <Frame title="浏览器连接">
        <div className="form-row">
          <label>连接方式</label>
          <Segments
            field="browser_mode"
            choices={[
              ["managed_cdp", "WPS 专用浏览器（推荐）"],
              ["cdp", "连接已打开的浏览器"],
            ]}
            value={config.browser_mode}
            onChange={(browser_mode) => {
              void persist({ browser_mode });
              setLogin("尚未检查");
            }}
          />
        </div>
        <div className="form-row">
          <label>浏览器连接地址</label>
          <TextField
            field="cdp_url"
            label="浏览器连接地址"
            value={config.cdp_url}
            onChange={(cdp_url) => {
              void persist({ cdp_url });
            }}
          />
        </div>
        <div className="form-row browser-actions">
          <label />
          <Button
            run={() => {
              void status("login");
            }}
          >
            登录 WPS
          </Button>
          <Button
            run={() => {
              void status("status");
            }}
          >
            刷新登录状态
          </Button>
          <span className="muted">{browserStatus}</span>
        </div>
        <div className="form-row short">
          <label>WPS 登录状态</label>
          <span>{login}</span>
        </div>
      </Frame>
      <Frame title="写入配置" className="wps-configurations">
        <div className="table-toolbar">
          <Button run={() => edit(-1)}>添加配置</Button>
          <Button
            run={() => {
              if (selected.length) edit(selected[0]);
            }}
          >
            编辑
          </Button>
          <Button
            run={() => {
              if (selected.length)
                void persist({
                  configs: [
                    ...configs,
                    {
                      ...copy(configs[selected[0]]),
                      name: configs[selected[0]].name + " 副本",
                    },
                  ],
                });
            }}
          >
            复制
          </Button>
          <Button
            run={() => {
              if (selected.length)
                void ask("确认删除选中的配置？", {
                  title: "删除配置",
                  kind: "warning",
                }).then((ok) => {
                  if (ok) {
                    void persist({
                      configs: configs.filter(
                        (_, index) => !selected.includes(index),
                      ),
                    });
                    setSelected([]);
                  }
                });
            }}
          >
            删除
          </Button>
          <span className="toolbar-gap" />
          <Button
            run={() => {
              void choosePath("wps.import", "", ["json"])
                .then(async (path) => {
                  if (path) {
                    const imported = await call<Values>("wps.import", { path });
                    await onConfig(imported);
                    setSelected([]);
                  }
                })
                .catch((error) => {
                  void message(String(error), { kind: "error" });
                });
            }}
          >
            导入
          </Button>
          <Button
            run={() => {
              void choosePath(
                "wps.export",
                "WPS写入配置.json",
                ["json"],
                false,
                false,
                true,
              )
                .then(async (path) => {
                  if (path) await call("wps.export", { path, config });
                })
                .catch((error) => {
                  void message(String(error), { kind: "error" });
                });
            }}
          >
            导出
          </Button>
          <span className="toolbar-spacer" />
          <Button
            run={() => {
              void move(-1);
            }}
          >
            上移
          </Button>
          <Button
            run={() => {
              void move(1);
            }}
          >
            下移
          </Button>
        </div>
        <DataTable
          headings={[
            "配置名称",
            "数据来源",
            "来源文件或链接",
            "目标表格链接",
            "区域数量",
            "首个写入区域",
          ]}
          rows={configs.map((row) => {
            const first = row.regions[0];
            return [
              row.name,
              row.source_type === "kdocs" ? "金山文档" : "本地表格",
              row.source_type === "kdocs" ? row.source_url : row.local_file,
              row.kdocs_url,
              row.regions.length,
              first
                ? `${first.source_sheet}!${first.source_start}:${first.source_end} -> ${first.target_sheet}!${first.target_start}:${first.target_end}`
                : "",
            ];
          })}
          selected={selected}
          onSelect={setSelected}
          onEdit={() => {
            if (selected.length) edit(selected[0]);
          }}
        />
        <div className="wps-execution">
          <span className="muted">
            {selected.length
              ? `已选择 ${selected.length} 个配置，共 ${selected.reduce((sum, index) => sum + (configs[index]?.regions.length ?? 0), 0)} 个写入区域。`
              : "请先选择配置，再预览或写入。"}
          </span>
          <span className="toolbar-spacer" />
          <Button
            run={() => {
              void apply(true);
            }}
          >
            预览
          </Button>
          <Button
            primary
            run={() => {
              void apply();
            }}
          >
            开始写入
          </Button>
        </div>
        <Check
          label="覆盖非空单元格"
          value={config.overwrite !== false}
          onChange={(overwrite) => {
            void persist({ overwrite });
          }}
        />
      </Frame>
      {editing && (
        <DocumentEditor
          key={editing.index}
          initial={editing.draft}
          config={config}
          onClose={() => setEditing(null)}
          execute={execute}
          save={(draft, close) => {
            const current = configRef.current;
            const rows = [...current.configs];
            let index = editIndex.current;
            if (index < 0) {
              index = rows.length;
              editIndex.current = index;
              rows.push(draft);
            } else rows[index] = draft;
            const titles = { ...current.document_titles };
            if (draft.source_url && draft.source_doc_title)
              titles[draft.source_url] = draft.source_doc_title;
            if (draft.kdocs_url && draft.target_doc_title)
              titles[draft.kdocs_url] = draft.target_doc_title;
            const urls = [
              ...new Set(
                [
                  draft.source_url,
                  draft.kdocs_url,
                  ...(current.recent_urls ?? []),
                ].filter(Boolean),
              ),
            ];
            void persist({
              configs: rows,
              document_titles: titles,
              recent_urls: urls,
            });
            setSelected([index]);
            if (close) setEditing(null);
          }}
        />
      )}
      {preview && (
        <Modal title="预览写入数据" wide onClose={() => setPreview(null)}>
          <p>{preview.message}</p>
          {preview.regions?.map((region: any) => (
            <Frame key={region.index} title={`${region.sheet}!${region.range}`}>
              <div className="preview-values">
                <table>
                  <tbody>
                    {region.values.map((row: any[], index: number) => (
                      <tr key={index}>
                        {row.map((value, cell) => (
                          <td key={cell}>{String(value ?? "")}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Frame>
          ))}
          <footer>
            <Button run={() => setPreview(null)}>关闭</Button>
          </footer>
        </Modal>
      )}
    </div>
  );
}
