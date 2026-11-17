import { useEffect, useRef, useState } from "react";
import { call } from "../api";
import { Button, Modal } from "./controls";
import type { Values } from "./types";

export function ManualMarket({
  reportDate,
  initial,
  onSave,
  onClose,
}: {
  reportDate: string;
  initial: Values;
  onSave: (value: Values) => void;
  onClose: () => void;
}) {
  const [fields, setFields] = useState<any[]>([]);
  const [period, setPeriod] = useState("day_ahead");
  const [data, setData] = useState<Values>({
    ...initial,
    report_date: reportDate,
  });
  const [preview, setPreview] = useState<any>(null);
  const [saving, setSaving] = useState(false);
  const [fieldsError, setFieldsError] = useState("");
  const inputs = useRef<HTMLDivElement>(null);
  const revision = useRef(0);
  async function loadFields() {
    setFieldsError("");
    try {
      setFields(await call<any[]>("ui.market.fields"));
    } catch (error) {
      setFieldsError(`无法加载填写字段：${String(error)}`);
    }
  }
  const [error, setError] = useState("");
  useEffect(() => {
    void loadFields();
  }, []);
  useEffect(() => {
    let stale = false;
    const currentRevision = revision.current;
    const timer = setTimeout(() => {
      void call<any>("ui.market.preview", { data, date: reportDate })
        .then((result) => {
          if (!stale && revision.current === currentRevision) {
            setPreview(result);
            setError("");
          }
        })
        .catch((error) => {
          if (!stale && revision.current === currentRevision) {
            setPreview(null);
            setError(String(error));
          }
        });
    }, 180);
    return () => {
      stale = true;
      clearTimeout(timer);
    };
  }, [data, reportDate]);
  function readManual() {
    const current = { ...data, [period]: { ...data[period] } };
    inputs.current
      ?.querySelectorAll<HTMLInputElement>("input[data-market-key]")
      .forEach((input) => {
        const key = input.dataset.marketKey!;
        current[period][key] = {
          ...current[period][key],
          [input.dataset.marketType!]: input.value,
        };
      });
    return current;
  }
  function change() {
    revision.current++;
    setData(readManual());
  }
  async function save() {
    if (saving) return;
    revision.current++;
    setSaving(true);
    try {
      // Always validate the actual controls, including the last edit before save.
      const result = await call<any>("ui.market.preview", {
        data: readManual(),
        date: reportDate,
      });
      onSave(result.data);
    } catch (error) {
      setPreview(null);
      setError(String(error));
    } finally {
      setSaving(false);
    }
  }
  const shift = (days: number) => {
    const day = new Date(reportDate + "T12:00:00");
    day.setDate(day.getDate() + days);
    return `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
  };
  return (
    <Modal title="手动填写数据" wide onClose={onClose}>
      <div className="tb-tabs" role="tablist">
        {[
          ["day_ahead", "日前市场"],
          ["realtime", "实时市场"],
        ].map(([key, name]) => (
          <button
            key={key}
            id={`market-tab-${key}`}
            role="tab"
            aria-selected={period === key}
            disabled={saving}
            onClick={() => {
              revision.current++;
              setData(readManual());
              setPeriod(key);
            }}
          >
            {name}
          </button>
        ))}
      </div>
      <div className="manual-market" ref={inputs}>
        <p className="muted">网站数据优先，手填数据仅补缺；留空表示不补录。</p>
        {fieldsError && (
          <div role="alert">
            {fieldsError}
            <Button
              run={() => {
                void loadFields();
              }}
            >
              重试加载
            </Button>
          </div>
        )}
        {!fields.length && !fieldsError && (
          <p role="status">正在加载填写字段…</p>
        )}
        <p className="muted">
          当前：{period === "day_ahead" ? reportDate : shift(-2)}{" "}
          {period === "day_ahead" ? "日前" : "实时"}　 对比：
          {period === "day_ahead" ? shift(-1) : shift(-2)} 日前
        </p>
        <table
          key={period}
          aria-label={period === "day_ahead" ? "日前市场数据" : "实时市场数据"}
        >
          <thead>
            <tr>
              {["指标", "当前值", "对比值", "增减（自动）"].map((label) => (
                <th key={label}>{label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {fields.map((field) => (
              <tr key={field.key}>
                <td>
                  {field.name}（{field.unit}）
                </td>
                {["value", "comparison"].map((type) => (
                  <td key={type}>
                    <input
                      id={`market-${period}-${field.key}-${type}`}
                      data-market-key={field.key}
                      data-market-type={type}
                      aria-label={`${field.name}${type === "value" ? "当前值" : "对比值"}`}
                      value={data[period]?.[field.key]?.[type] ?? ""}
                      disabled={saving}
                      onInput={change}
                    />
                  </td>
                ))}
                <td>
                  {formatMarketDelta(preview?.deltas?.[period]?.[field.key])}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <Button
          disabled={saving || !fields.length}
          run={() => {
            revision.current++;
            setData({ ...data, [period]: {} });
          }}
        >
          清空本页
        </Button>
        {error && <p role="alert">{error}</p>}
      </div>
      <footer>
        <Button disabled={saving} run={onClose}>
          取消
        </Button>
        <Button
          primary
          disabled={saving || !fields.length || !!fieldsError}
          run={() => {
            void save();
          }}
        >
          {saving ? "正在校验…" : "保存并返回"}
        </Button>
      </footer>
    </Modal>
  );
}

export function formatMarketDelta(value?: number | null): string {
  return value == null ? "—" : value > 0 ? `+${value}` : String(value);
}
