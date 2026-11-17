import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { open, save } from "@tauri-apps/plugin-dialog";
import { call } from "../api";

export function Button({
  children,
  run,
  primary = false,
  icon = false,
  ...props
}: {
  children: ReactNode;
  run?: () => void;
  primary?: boolean;
  icon?: boolean;
  [key: string]: any;
}) {
  return (
    <button
      type="button"
      {...props}
      className={`tb-button ${primary ? "primary" : ""} ${icon ? "file-button" : ""} ${props.className ?? ""}`}
      onClick={run}
      title={
        props.title ?? (typeof children === "string" ? children : undefined)
      }
      aria-label={
        icon && typeof children === "string" ? children : props["aria-label"]
      }
    >
      {icon ? <span aria-hidden="true">+</span> : children}
    </button>
  );
}
export function Frame({
  title,
  children,
  className = "",
}: {
  title?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`tb-frame ${className}`} aria-label={title}>
      {title && <div className="frame-caption">{title}</div>}
      <div className="frame-body">{children}</div>
    </section>
  );
}
export function Check({
  label,
  value,
  onChange,
  option = false,
  disabled = false,
  field,
}: {
  label: string;
  value: boolean;
  onChange: (value: boolean) => void;
  option?: boolean;
  disabled?: boolean;
  field?: string;
}) {
  return (
    <label
      className={`tb-check ${option ? "option" : ""} ${value ? "selected" : ""}`}
    >
      <input
        type="checkbox"
        data-field={field}
        checked={value}
        onChange={(event) => onChange(event.target.checked)}
        disabled={disabled}
      />
      <span>{label}</span>
    </label>
  );
}
export function Segments({
  choices,
  value,
  onChange,
  field,
  disabled = false,
}: {
  choices: string[][];
  value: string;
  onChange: (value: string) => void;
  field?: string;
  disabled?: boolean;
}) {
  return (
    <div className="tb-segments" role="radiogroup">
      {choices.map(([key, label]) => (
        <label key={key} className={value === key ? "selected" : ""}>
          <input
            type="radio"
            data-field={field}
            name={field}
            value={key}
            checked={value === key}
            onChange={() => onChange(key)}
            disabled={disabled}
          />
          <span>{label}</span>
        </label>
      ))}
    </div>
  );
}
export function Modal({
  title,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const last = document.activeElement as HTMLElement | null;
    ref.current?.showModal();
    return () => {
      last?.focus();
    };
  }, []);
  return createPortal(
    <dialog
      ref={ref}
      aria-label={title}
      className={`tb-modal ${wide ? "wide" : ""}`}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <header>
        <h2>{title}</h2>
        <Button id={`modal-close-${title}`} run={onClose} aria-label="关闭">
          ×
        </Button>
      </header>
      {children}
    </dialog>,
    document.body,
  );
}

export async function choosePath(
  key: string,
  current: string,
  extensions: string[] = ["xlsx", "xls"],
  directory = false,
  multiple = false,
  saveFile = false,
): Promise<string | string[] | null> {
  const initial = await call<string>("ui.dialog.directory", { key, current });
  const options = {
    title: directory ? "选择目录" : saveFile ? "选择保存位置" : "选择文件",
    defaultPath: saveFile && current ? current : initial,
    filters: directory ? undefined : [{ name: "支持的文件", extensions }],
  };
  const selected = saveFile
    ? await save(options)
    : await open({ ...options, directory, multiple });
  if (selected)
    await call("ui.dialog.remember", {
      key,
      path: Array.isArray(selected) ? selected[0] : selected,
      directory,
    });
  return selected;
}

const iso = (date: Date) =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
const parsed = (value: string) =>
  /^\d{4}-\d{2}-\d{2}$/.test(value)
    ? new Date(`${value}T12:00:00`)
    : new Date();
export function DateFields({
  start,
  end,
  onChange,
  range = true,
  disabled = false,
  id = "dates",
  optional = false,
}: {
  start: string;
  end?: string;
  onChange: (start: string, end?: string) => void;
  range?: boolean;
  disabled?: boolean;
  id?: string;
  optional?: boolean;
}) {
  const [popup, setPopup] = useState<{
    left: number;
    top: number;
    stage: "start" | "end";
  } | null>(null);
  const [month, setMonth] = useState(
    () => new Date(parsed(start).getFullYear(), parsed(start).getMonth(), 1),
  );
  const [right, setRight] = useState(
    () => new Date(month.getFullYear(), month.getMonth() + 1, 1),
  );
  const [linked, setLinked] = useState(true);
  const box = useRef<HTMLDivElement>(null);
  const a = useRef<HTMLInputElement>(null);
  const b = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (a.current) a.current.value = start;
  }, [start]);
  useEffect(() => {
    if (b.current) b.current.value = end ?? "";
  }, [end]);
  useEffect(() => {
    if (!popup) return;
    const outside = (event: PointerEvent) => {
      if (
        !box.current?.contains(event.target as Node) &&
        event.target !== a.current &&
        event.target !== b.current
      )
        setPopup(null);
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [popup]);
  function show(side: "start" | "end") {
    if (disabled) return;
    const rect = (side === "start" ? a : b).current!.getBoundingClientRect();
    const current = parsed(
      side === "start"
        ? (a.current?.value ?? start)
        : (b.current?.value ?? end ?? start),
    );
    const left = new Date(current.getFullYear(), current.getMonth(), 1);
    setMonth(left);
    setRight(new Date(left.getFullYear(), left.getMonth() + 1, 1));
    setPopup({
      left: Math.max(
        8,
        Math.min(rect.left, innerWidth - (range ? 640 : 320) - 8),
      ),
      top: Math.max(8, Math.min(rect.bottom + 3, innerHeight - 350)),
      stage: side,
    });
  }
  async function normalize(input: HTMLInputElement, side: "start" | "end") {
    if (!input.value.trim()) return;
    const original = input.value;
    try {
      const value = await call<string>("ui.date.normalize", {
        value: original,
      });
      if (!input.isConnected || input.value !== original) return;
      input.value = value;
      onChange(
        side === "start" ? value : (a.current?.value ?? start),
        side === "end" ? value : (b.current?.value ?? end),
      );
    } catch {}
  }
  function pick(value: string) {
    if (!range) {
      a.current!.value = value;
      onChange(value);
      setPopup(null);
      a.current?.focus();
      return;
    }
    if (popup?.stage === "start") {
      a.current!.value = value;
      onChange(value, b.current?.value ?? end);
      setPopup({ ...popup, stage: "end" });
    } else {
      let first = a.current?.value ?? start;
      let last = value;
      if (first && last < first) {
        last = first;
        first = value;
      }
      a.current!.value = first;
      b.current!.value = last;
      onChange(first, last);
      setPopup(null);
      b.current?.focus();
    }
  }
  function change(side: number, delta: number) {
    const base = side === 0 ? month : right;
    const next = new Date(base.getFullYear(), base.getMonth() + delta, 1);
    if (linked) {
      const first =
        side === 0
          ? next
          : new Date(next.getFullYear(), next.getMonth() - 1, 1);
      setMonth(first);
      setRight(new Date(first.getFullYear(), first.getMonth() + 1, 1));
    } else side === 0 ? setMonth(next) : setRight(next);
  }
  const calendar = (current: Date, side: number) => {
    const offset =
      (new Date(current.getFullYear(), current.getMonth(), 1).getDay() + 6) % 7;
    return (
      <div className="calendar-month">
        <div className="calendar-nav">
          <Button
            run={() => change(side, -12)}
            aria-label={`${side ? "右侧" : "左侧"}上一年`}
          >
            «
          </Button>
          <Button
            run={() => change(side, -1)}
            aria-label={`${side ? "右侧" : "左侧"}上个月`}
          >
            ‹
          </Button>
          <strong>
            {current.getFullYear()}年{current.getMonth() + 1}月
          </strong>
          <Button
            run={() => change(side, 1)}
            aria-label={`${side ? "右侧" : "左侧"}下个月`}
          >
            ›
          </Button>
          <Button
            run={() => change(side, 12)}
            aria-label={`${side ? "右侧" : "左侧"}下一年`}
          >
            »
          </Button>
        </div>
        <div className="calendar-grid">
          {"一二三四五六日".split("").map((day) => (
            <span key={day}>{day}</span>
          ))}
          {Array.from({ length: 42 }, (_, n) => {
            const day = new Date(
              current.getFullYear(),
              current.getMonth(),
              n - offset + 1,
            );
            const value = iso(day);
            return (
              <button
                type="button"
                key={value}
                id={`${id}-calendar-${side}-${value}`}
                aria-label={`${side ? "右侧" : "左侧"} ${value}`}
                className={`${day.getMonth() !== current.getMonth() ? "adjacent" : ""} ${value === start || value === end ? "selected" : ""} ${start && end && value > start && value < end ? "in-range" : ""}`}
                onClick={() => pick(value)}
              >
                {day.getDate()}
              </button>
            );
          })}
        </div>
      </div>
    );
  };
  return (
    <span className="tb-dates">
      <input
        ref={a}
        id={`${id}-start`}
        aria-label={range ? "开始日期" : "运行日期"}
        data-field={range ? "start_date" : id}
        defaultValue={start}
        disabled={disabled}
        onClick={() => show("start")}
        onBlur={(event) => {
          void normalize(event.target, "start");
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown") {
            event.preventDefault();
            show("start");
          }
          if (event.key === "Escape") setPopup(null);
          if (event.key === "Enter") setPopup(null);
        }}
      />
      {range && (
        <>
          <span>至</span>
          <input
            ref={b}
            id={`${id}-end`}
            aria-label="结束日期"
            data-field="end_date"
            defaultValue={end}
            disabled={disabled}
            onClick={() => show("end")}
            onBlur={(event) => {
              void normalize(event.target, "end");
            }}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") {
                event.preventDefault();
                show("end");
              }
              if (event.key === "Escape" || event.key === "Enter")
                setPopup(null);
            }}
          />
        </>
      )}
      {popup &&
        createPortal(
          <div
            ref={box}
            className="tb-calendar"
            role="dialog"
            aria-label="选择日期"
            style={{ left: popup.left, top: popup.top }}
            onKeyDown={(event) => {
              if (event.key === "Escape") setPopup(null);
            }}
          >
            <div className="calendar-months">
              {calendar(month, 0)}
              {range && calendar(right, 1)}
            </div>
            {range && (
              <div className="calendar-foot">
                <span>
                  {popup.stage === "start"
                    ? "请选择开始日期"
                    : "请选择结束日期"}
                  {optional ? "（开始日期可留空）" : ""}
                </span>
                <Check label="翻月联动" value={linked} onChange={setLinked} />
              </div>
            )}
            <Button run={() => pick(iso(new Date()))}>今天</Button>
          </div>,
          document.body,
        )}
    </span>
  );
}
