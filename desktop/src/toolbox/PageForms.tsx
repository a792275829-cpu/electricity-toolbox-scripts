import { invoke } from "@tauri-apps/api/core";
import { message } from "@tauri-apps/plugin-dialog";
import { call } from "../api";
import { useEffect, useRef, type CSSProperties } from "react";
import {
  Button,
  Check,
  DateFields,
  Frame,
  Segments,
  choosePath,
} from "./controls";
import type { Tool, Values } from "./types";

export function TextField({
  field,
  value,
  onChange,
  disabled = false,
  type = "text",
  label,
  readOnly = false,
  list,
}: {
  field: string;
  value: any;
  onChange: (value: string) => void;
  disabled?: boolean;
  type?: string;
  label?: string;
  readOnly?: boolean;
  list?: string;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current && ref.current.value !== String(value ?? ""))
      ref.current.value = String(value ?? "");
  }, [value]);
  return (
    <input
      ref={ref}
      id={`field-${field}`}
      data-field={field}
      aria-label={label ?? field}
      type={type}
      defaultValue={value ?? ""}
      onInput={(event) => onChange(event.currentTarget.value)}
      disabled={disabled}
      readOnly={readOnly}
      list={list}
    />
  );
}

export const notes: Record<string, string> = {
  "spot-price":
    "华能（潮州潮安）新能源有限责任公司 → 归湖；华能（汕头金平）新能源有限责任公司 → 鮀莲。\n华能广东汕头海上风电有限责任公司（广澳海风场）→ 海风。\n日前出清电价 → 日前价格 B:Y；实时出清电价 → 实时价格 B:Y。\n超出表尾时连续接续日期行并沿用格式；已有日期按覆盖设置处理。\n日前、实时逐日独立更新；缺少完整 24 点的数据跳过，详情见日志。",
  "transaction-info":
    "日前 D，实际 D-3，按日期接续更新 Sheet1。\n左侧日前：统调、B类、A类、地方电、粤港联络线；西电行保留但数值留空。\n日前 B 类直接取网站数值，不扣减实际西电。\n右侧实际：写入六类数据。每类依次为最大、最小、平均、总电量。",
  "power-benefit":
    "归湖/鮀莲：C 原始功率；J 申报功率；K 日前电量；L 日前电价；M 上网电量；N 实时电价。\n海风：C 原始功率；R 申报功率；L 日前电量；N 上网电量；M 日前电价；O 实时电价。覆盖所选日期内六列的值及公式。\n均按运行日期匹配 0–23 点，每列 24 个数。归湖、鮀莲默认只补空白；勾选“覆盖非空单元格”后覆盖已有数值，公式单元格仍跳过。\n模板缺少所选日期或月份时，列出缺失日期并停止，不会仅更新部分日期。\n保留模板格式及其他收益公式；未发布字段保留原值，后续运行继续更新。",
  "daily-pdf-parser":
    "容量按图像估算，细小分段可能有误差。\nPDF → 现货交易日报；Excel → 报价容量数据，均按 PDF 报告日期匹配。",
};
const mappings = [
  "现货运行日报 > 市场负荷信息 > 日前：来源日期为基准日期 D；写入 2026年市场情况 工作表对应日期行 B:U。",
  "现货运行日报 > 日前市场 > 发电侧申报均价起 12 项：来源日期为基准日期 D-1；写入 2026年市场情况 工作表对应日期行 V:AG。",
  "现货运行日报 > 市场负荷信息 > 实际：来源日期为基准日期 D-2；写入 2026年市场情况 工作表对应日期行 AH:BA。",
  "现货运行日报 > 实时市场 > 发电侧成交电量、发电侧加权均价、燃煤平均成交电价、燃气平均成交电价：来源日期为基准日期 D-2；写入 2026年市场情况 工作表对应日期行 BB:BE。",
  "现货运行日报 > 发电侧结算 > 12 项：来源日期为基准日期 D-6；写入 2026年市场情况 工作表对应日期行 BF:BQ。",
  "现货运行日报 > 用电侧结算 > 7 项：来源日期为基准日期 D-6；写入 2026年市场情况 工作表对应日期行 BR:BX。",
  "现货基础信息报送 > 机组成本信息 > 单位变动成本对应电价：来源日期为基准日期 D；写入 2026年运行方式及成本 工作表对应日期行 N:X。",
  "省内现货交易 > 市场行情看板 > 在线机组数、省内机组预测检修容量：来源日期为基准日期 D；写入 2026年运行方式及成本 工作表对应日期行 AA、AE。",
  "公有数据管理 > 现货分时分类型出清电量 > 日前：燃煤取 23:00 开机台数，燃气取全天开机台数最大值；来源日期为基准日期 D；写入 2026年运行方式及成本 工作表对应日期行 AB:AC。",
  "2026年运行方式及成本 > 机组运行方式：来源日期为基准日期 D-1；复制 B:M 到基准日期 D 对应日期行 B:M。",
];

type Props = {
  tool: Tool;
  values: Values;
  setValues: (patch: Values) => void;
  busy: boolean;
  run: (action?: string) => void;
  openOutput: () => void;
  autoMatch: () => void;
  manual: () => void;
  editor: () => void;
  reportDate: (path: string) => void;
};
export function PageForms(props: Props) {
  const {
    tool,
    values: v,
    setValues: update,
    busy,
    run,
    openOutput,
    autoMatch,
    manual,
    editor,
    reportDate,
  } = props;
  const id = tool.id;
  const action = (
    label: string,
    fn = () => run(),
    primary = false,
    icon = false,
  ) => (
    <Button
      key={label}
      primary={primary}
      icon={icon}
      disabled={busy && !label.startsWith("打开")}
      run={fn}
    >
      {label}
    </Button>
  );
  const businessAction = (
    label: string,
    taskAction = "run",
    primary = true,
  ) => (
    <Button
      id={`${id}-action-${taskAction}`}
      data-action={taskAction}
      primary={primary}
      disabled={busy}
      run={() => run(taskAction)}
    >
      {label}
    </Button>
  );
  const options = (
    <div className="form-options">
      <Check
        label="覆盖非空单元格"
        field="overwrite"
        value={!!v.overwrite}
        onChange={(overwrite) => update({ overwrite })}
      />
      <Check
        label="写入前生成时间戳备份"
        field="create_backup"
        value={!!v.create_backup}
        onChange={(create_backup) => update({ create_backup })}
      />
    </div>
  );
  const dates = (
    label: string,
    single?: string,
    optional = false,
    hint = "",
  ) => (
    <div className="form-row">
      <label>{label}</label>
      <DateFields
        key={id + single}
        id={single ?? id}
        start={String(v[single ?? "start_date"] ?? "")}
        end={String(v.end_date ?? "")}
        range={!single}
        optional={optional}
        disabled={busy}
        onChange={(start, end) =>
          update(
            single ? { [single]: start } : { start_date: start, end_date: end },
          )
        }
      />
      {hint && <span className="muted inline-hint">{hint}</span>}
    </div>
  );
  async function choose(
    field: string,
    directory = false,
    multiple = false,
    extensions?: string[],
    saveFile = false,
  ) {
    try {
      const selected = await choosePath(
        `${id}.${field}`,
        String(v[field] ?? ""),
        extensions ??
          tool.fields.find((f) => f.key === field)?.extensions ?? [
            "xlsx",
            "xls",
          ],
        directory,
        multiple,
        saveFile,
      );
      if (selected) {
        if (id === "group-upload" && field === "files") {
          update(
            await call("ui.group.inspect", {
              files: Array.isArray(selected) ? selected : [selected],
            }),
          );
          return;
        }
        update({
          [field]: multiple
            ? [
                ...new Set([
                  ...(v[field] ?? []),
                  ...(Array.isArray(selected) ? selected : [selected]),
                ]),
              ]
            : selected,
        });
        if (id === "daily-pdf-parser" && field === "pdf_path")
          update({
            output_path: await call<string>("ui.daily.output", {
              path: String(selected),
            }),
          });
        if (id === "market-report" && field === "day_report")
          reportDate(String(selected));
      }
    } catch (error) {
      await message(String(error), { title: "选择文件失败", kind: "error" });
    }
  }
  const path = (
    field: string,
    label: string,
    directory = false,
    button = "选择文件",
    extensions?: string[],
    saveFile = false,
  ) => (
    <div className="form-row path-row" key={field}>
      <label htmlFor={`field-${field}`}>{label}</label>
      <TextField
        field={field}
        label={label}
        value={v[field]}
        onChange={(value) => update({ [field]: value })}
      />
      <Button
        icon
        run={() => {
          if (button === "打开目录") {
            void call<string>("ui.business.directory", {
              path: String(v[field] ?? ""),
              workbook: field === "master_workbook",
            })
              .then((path) => invoke("open_path", { path }))
              .catch((error) =>
                message(String(error), { title: "目录无效", kind: "error" }),
              );
          } else void choose(field, directory, false, extensions, saveFile);
        }}
        disabled={busy}
      >
        {button}
      </Button>
    </div>
  );
  const files = (title?: string) => (
    <div className="file-list-container">
      {title && <div className="frame-caption">{title}</div>}
      <select
        multiple
        aria-label={title ?? "待处理文件"}
        className="file-list"
        size={12}
        onChange={(event) =>
          update({
            _selected_files: Array.from(event.target.selectedOptions).map(
              (option) => option.value,
            ),
          })
        }
      >
        {(v.files ?? []).map((file: string) => (
          <option key={file}>{file}</option>
        ))}
      </select>
    </div>
  );
  const remove = () =>
    update({
      files: (v.files ?? []).filter(
        (file: string) => !(v._selected_files ?? []).includes(file),
      ),
    });
  const units = (
    <div className="form-options">
      <span>更新场站：</span>
      {["归湖", "鮀莲", "海风"].map((name, n) => {
        const key = ["guihu", "tuolian", "haifeng"][n] + "_selected";
        return (
          <Check
            key={key}
            label={name}
            option
            field={key}
            value={!!v[key]}
            onChange={(value) => update({ [key]: value })}
          />
        );
      })}
      {id === "power-benefit" && (
        <span className="muted">
          海风覆盖必须填写开始日期；其他场站留空检查历史空白
        </span>
      )}
    </div>
  );
  let toolbar: any;
  let body: any;
  if (id === "online-energy" || id === "wind-forecast") {
    toolbar = (
      <>
        {businessAction(id === "online-energy" ? "开始导出" : "生成截图")}
        {action("打开输出目录", openOutput)}
      </>
    );
    body = (
      <Frame
        title={id === "online-energy" ? "导出设置" : "抓取设置"}
        className="single-row"
      >
        {dates(
          id === "online-energy" ? "导出日期" : "运行日期",
          id === "wind-forecast" ? "run_date" : undefined,
        )}
      </Frame>
    );
  } else if (id === "market-table") {
    toolbar = businessAction("开始更新");
    body = (
      <>
        <Frame title="更新设置">
          {dates("基准运行日期", "run_date")}
          {path("workbook", "市场 Excel")}
          {options}
        </Frame>
        <Frame title="当前已实现映射" className="grow">
          <div className="mapping-text" tabIndex={0}>
            {mappings.map((line, n) => (
              <div key={line}>
                {n + 1}. {line}
              </div>
            ))}
          </div>
        </Frame>
      </>
    );
  } else if (["spot-price", "power-benefit", "transaction-info"].includes(id)) {
    toolbar = (
      <>
        {businessAction("抓取并更新")}
        {id !== "spot-price" && action("打开工作簿目录", openOutput)}
      </>
    );
    body = (
      <>
        <Frame
          title="更新设置"
          className={id === "transaction-info" ? "" : "compact"}
        >
          {id === "transaction-info" ? (
            <>
              {path("workbook", "目标工作簿")}
              {dates(
                "日前日期范围",
                undefined,
                true,
                "开始日期可留空；指定历史日期可修订",
              )}
            </>
          ) : (
            <>
              {dates(
                id === "spot-price" ? "更新日期" : "日期范围",
                undefined,
                id === "power-benefit",
                id === "power-benefit" ? "结束日期为 D" : "",
              )}
              {["归湖", "鮀莲", "海风"].map((name, n) =>
                path(
                  ["guihu", "tuolian", "haifeng"][n] + "_workbook",
                  name + "工作簿",
                ),
              )}
              {units}
            </>
          )}
          {options}
        </Frame>
        <Frame
          title={id === "spot-price" ? "固定映射" : "更新口径"}
          className="compact"
        >
          <p className="verbatim">{notes[id]}</p>
        </Frame>
      </>
    );
  } else if (id === "guangdong-price") {
    toolbar = (
      <>
        {businessAction("补齐数据总表", "update")}
        {businessAction("运行日前预测", "predict", false)}
        {action("打开预测目录", openOutput)}
      </>
    );
    body = (
      <>
        <Frame title="1. 补齐历史数据总表" className="compact">
          {dates("运行日 D", "run_date")}
          {path("master_workbook", "数据总表", false, "打开目录")}
          <p className="muted">
            仅保留并补齐截至 D 日的近一年数据，范围外日期截断；每天 96 个时点。
          </p>
        </Frame>
        <Frame title="2. 使用总表预测 D+1 至 D+15" className="compact">
          <div className="form-row">
            <label>建模输入表</label>
            <span className="wrap">{v.master_workbook}</span>
          </div>
          {path("prediction_output_dir", "预测输出目录", true, "选择目录")}
          <div className="form-row">
            <label>滚动训练月数</label>
            <TextField
              field="training_months"
              label="滚动训练月数"
              type="number"
              value={v.training_months}
              onChange={(value) => update({ training_months: value })}
            />
            <span className="muted">
              0 为自动：当日供需齐全用 12 个月，否则用 3
              个月历史信息回退；手工填写最少 1 个月。
            </span>
          </div>
          <div className="form-row">
            <label>预测天数</label>
            <TextField
              field="forecast_days"
              label="预测天数"
              type="number"
              value={v.forecast_days}
              onChange={(value) => update({ forecast_days: value })}
            />
            <span className="muted">
              1 只输出 D+1，15 输出 D+1 至 D+15；D+2
              起没有已发布供需，误差随天数放大。
            </span>
          </div>
        </Frame>
      </>
    );
  } else if (id === "summary-upload" || id === "daily-pdf-parser") {
    const summary = id === "summary-upload";
    const labels = summary
      ? { summary: "开始汇总", upload: "开始上传", both: "汇总并上传" }
      : { both: "解析并上传", parse: "开始解析", upload: "上传已有文件" };
    toolbar = (
      <>
        {businessAction((labels as any)[v.mode] ?? "开始执行")}
        {summary && businessAction("预览上传文件", "preview", false)}
        {action("打开输出目录", openOutput)}
      </>
    );
    body = (
      <>
        <Frame title={summary ? "数据与功能" : undefined}>
          <div className="form-row">
            <label>执行功能</label>
            <Segments
              field="mode"
              choices={
                summary
                  ? [
                      ["summary", "仅汇总"],
                      ["upload", "仅上传"],
                      ["both", "汇总并上传"],
                    ]
                  : [
                      ["both", "解析并上传"],
                      ["parse", "仅解析"],
                      ["upload", "仅上传"],
                    ]
              }
              value={v.mode}
              onChange={(mode) => update({ mode })}
            />
          </div>
          {summary ? (
            path("input_dir", "复盘文件夹", true, "选择文件夹")
          ) : (
            <>
              {path("pdf_path", "运行日报 PDF", false, "选择 PDF", ["pdf"])}
              {path(
                "output_path",
                "输出 Excel",
                false,
                "选择位置",
                ["xlsx"],
                true,
              )}
            </>
          )}
        </Frame>
        {!summary && (
          <Frame title="输出说明" className="compact">
            <p className="verbatim">{notes[id]}</p>
          </Frame>
        )}
      </>
    );
  } else if (id === "trade-analysis" || id === "section-analysis") {
    const section = id === "section-analysis";
    toolbar = (
      <>
        {action(
          section ? "添加电价文件" : "添加文件",
          () => {
            void choose("files", false, true, ["xlsx"]);
          },
          false,
          true,
        )}
        {!section && action("自动匹配D和D-2", autoMatch)}
        {!section && businessAction("开始分析")}
        {section && action("移除选中", remove)}
        {action("清空列表", () => update({ files: [] }))}
        {!section && action("移除选中", remove)}
        {section && (
          <>
            {action("浏览器编辑", editor)}
            {businessAction("检修登录", "login", false)}
            {businessAction("开始分析")}
          </>
        )}
        {action(section ? "打开结果目录" : "打开输出目录", openOutput)}
      </>
    );
    body = (
      <>
        {files(section ? "节点电价文件（支持批量）" : undefined)}
        {section ? (
          <Frame title="分析设置">
            {path("topology_path", "拓扑名单", false, "选择文件", [
              "md",
              "json",
            ])}
            {path("output_root", "结果目录", true, "选择目录")}
            <div className="form-row">
              <label htmlFor="field-threshold">价差阈值</label>
              <TextField
                field="threshold"
                label="价差阈值"
                type="number"
                value={v.threshold}
                onChange={(threshold) => update({ threshold })}
              />
              <span className="muted">元/MWh，严格大于</span>
            </div>
          </Frame>
        ) : (
          path("output_dir", "输出目录", true, "选择目录")
        )}
      </>
    );
  } else if (id === "report" || id === "market-report") {
    const market = id === "market-report";
    toolbar = (
      <>
        {businessAction(market ? "生成日报" : "开始生成")}
        {action("打开输出目录", openOutput)}
        {market && action("手动填写数据", manual)}
      </>
    );
    const inner = (
      <>
        {market ? (
          <div className="form-row">
            <label>报告日期</label>
            <TextField
              field="_report_date"
              label="报告日期"
              value={v._report_date ?? ""}
              onChange={() => {}}
              readOnly
            />
            <span className="muted">选择 D 日收益表后自动识别</span>
          </div>
        ) : (
          <div className="form-row">
            {dates("报告日期", "report_date")}
            {action("按日期自动匹配 Excel", autoMatch)}
          </div>
        )}
        {market ? (
          <>
            {path("day_report", "D 日收益表")}
            {path("previous_report", "D-1 日收益表")}
            {path("d2_report", "D-2 日收益表")}
          </>
        ) : (
          <>
            {path("online_workbook", "前一日 Excel（上网电量）", false, "浏览")}
            {path(
              "day_ahead_workbook",
              "报告日 Excel（日前电量/价格）",
              false,
              "浏览",
            )}
            {path("daily_workbook", "前两日 Excel（日清数据）", false, "浏览")}
            {path("template_report", "Word 模板（可选）", false, "浏览", [
              "docx",
            ])}
          </>
        )}
      </>
    );
    body = market ? (
      <Frame title="输入">{inner}</Frame>
    ) : (
      <>
        <div className="report-form">{inner}</div>
        <div className="form-options">
          <Check
            option
            label="抓取网站数据并汇总 Excel"
            field="fetch_data"
            value={v.fetch_data}
            onChange={(fetch_data) => update({ fetch_data })}
          />
          <Check
            option
            label="生成 Word 报告"
            field="generate_word"
            value={v.generate_word}
            onChange={(generate_word) => update({ generate_word })}
          />
        </div>
      </>
    );
  } else if (id === "group-upload" || id === "private-batch-upload") {
    const group = id === "group-upload";
    toolbar = (
      <>
        {action(
          "选择文件",
          () => {
            void choose(
              "files",
              false,
              !group,
              group ? ["xlsx", "xls"] : ["xlsx", "xls", "csv", "pdf", "zip"],
            );
          },
          false,
          true,
        )}
        {businessAction("开始上传")}
        <span className="toolbar-spacer" />
        {group ? (
          <Check
            label="已上传数据也覆盖"
            field="force"
            value={v.force}
            onChange={(force) => update({ force })}
          />
        ) : (
          <Check
            label="上传后回读数据状态"
            field="verify"
            value={v.verify}
            onChange={(verify) => update({ verify })}
          />
        )}
      </>
    );
    body = (
      <Frame title={group ? "待上传文件" : "上传单元与文件"}>
        {group ? (
          <p>{v._summary ?? "请选择能销数据；省内数据使用内置模板自动生成。"}</p>
        ) : (
          <>
            <div className="form-row">
              <label>上传单元</label>
              <Segments
                field="unit"
                choices={["海风", "鮀莲", "归湖", "东莞", "汕头", "海门"].map(
                  (name) => [name, name],
                )}
                value={v.unit}
                onChange={(unit) => update({ unit })}
              />
            </div>
            <div className="form-row">
              <label>账单类型</label>
              <Check
                label="月度结算单"
                value={v.summary_type !== "none"}
                onChange={(checked) =>
                  update({ summary_type: checked ? "formal" : "none" })
                }
              />
              <Segments
                field="summary_type"
                choices={[
                  ["formal", "正式账单"],
                  ["temporary", "临时账单"],
                ]}
                value={v.summary_type}
                disabled={v.summary_type === "none"}
                onChange={(summary_type) => update({ summary_type })}
              />
            </div>
          </>
        )}
        <p className="muted file-selection">
          {(v.files ?? []).length
            ? (v._selection ?? (v.files as string[]).join("\n"))
            : "未选择文件"}
        </p>
      </Frame>
    );
  }
  return (
    <>
      <div className="tool-toolbar">
        {toolbar}
      </div>
      <div
        className={`page-content ${id}`}
        style={
          {
            "--label-width":
              id === "report"
                ? "159px"
                : id === "market-report"
                  ? "71px"
                  : "70px",
          } as CSSProperties
        }
      >
        {body}
      </div>
    </>
  );
}
