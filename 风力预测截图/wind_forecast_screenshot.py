#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright


SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = SCRIPT_DIR.parent
if str(WORKSPACE_DIR) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_DIR))
from toolbox_shared.auth_loader import load_login_module, standalone_login
from toolbox_shared.credentials import apply_credentials
from toolbox_shared.dates import normalize_date_input

BASE_URL = "https://xhxt.chng.com.cn"
APP_URL = f"{BASE_URL}/gdfire/PrivateDataManageCE/SpotBasicInfoSubmit"
API_URL = f"{BASE_URL}/gdfire/api/data/personal/newEnergy/basic/query/v1"
from toolbox_shared.storage import data_path, wind_forecast_output_dir

PROFILE_DIR = data_path(SCRIPT_DIR / ".browser-profile")
AUTH_STATE_PATH = data_path(SCRIPT_DIR / "auth_state.json")
AUTH_LOCK_PATH = Path(str(AUTH_STATE_PATH) + ".lock")
CONFIG_PATH = data_path(SCRIPT_DIR / "config.json")
FALLBACK_CONFIG_PATH = data_path(WORKSPACE_DIR / "上网电量抓取" / "config.json")
OUTPUT_DIR = wind_forecast_output_dir()

DEFAULT_ORG_ID = "e4c88ecc8ec18540018eeb6d767241fe"
DEFAULT_ORG_NAME = "华能广东汕头海上风电有限责任公司"
DEFAULT_UNIT_ID = "e4d5a0e78ecbe1f5018eeb93cff4392c"
DEFAULT_UNIT_NAME = "广澳海风场"
EXPECTED_POINT_COUNT = 96
EXPECTED_DAY_COUNT = 7


class ForecastDataUnavailable(RuntimeError):
    """The query completed, but no complete seven-day forecast is available."""


@dataclass(frozen=True, slots=True)
class Config:
    username: str = ""
    password: str = ""
    headless: bool = True
    output_dir: Path = OUTPUT_DIR
    org_id: str = DEFAULT_ORG_ID
    org_name: str = DEFAULT_ORG_NAME
    unit_id: str = DEFAULT_UNIT_ID
    unit_name: str = DEFAULT_UNIT_NAME


@dataclass(frozen=True, slots=True)
class ForecastRow:
    forecast_date: str
    daily_total: float
    daily_average: float
    hours: tuple[float, ...]


def log(message: str) -> None:
    print(message, flush=True)


def validate_date(value: str) -> str:
    return normalize_date_input(value)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def load_config() -> Config:
    fallback = _load_json(FALLBACK_CONFIG_PATH)
    own = _load_json(CONFIG_PATH)
    raw = apply_credentials({**fallback, **own})
    # Only the wind tool's own output setting applies; the login fallback may
    # contain another tool's export directory.
    output_dir = wind_forecast_output_dir(own.get("outputDir"), script_dir=SCRIPT_DIR)
    return Config(
        username=str(raw.get("username") or ""),
        password=str(raw.get("password") or ""),
        headless=bool(raw.get("headless", True)),
        output_dir=output_dir,
        org_id=str(raw.get("orgId") or DEFAULT_ORG_ID),
        org_name=str(raw.get("orgName") or DEFAULT_ORG_NAME),
        unit_id=str(raw.get("unitId") or DEFAULT_UNIT_ID),
        unit_name=str(raw.get("unitName") or DEFAULT_UNIT_NAME),
    )


def load_auth_module():
    return load_login_module(
        WORKSPACE_DIR,
        module_name="wind_forecast_auth",
        state_dir=SCRIPT_DIR,
        config_path=CONFIG_PATH,
        app_url=APP_URL,
    )


def _number(value: Any, *, label: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label}不是有效数字：{value!r}") from exc


def build_forecast_row(item: dict[str, Any]) -> ForecastRow:
    date_text = str(item.get("date") or "")[:10]
    validate_date(date_text)
    values = item.get("modifyForecastPower") or item.get("forecastPower") or []
    if not isinstance(values, list) or len(values) != EXPECTED_POINT_COUNT:
        raise RuntimeError(
            f"预测日期 {date_text} 应有 {EXPECTED_POINT_COUNT} 个15分钟点，实际为 "
            f"{len(values) if isinstance(values, list) else 0} 个"
        )
    numbers = [_number(value, label=f"{date_text} 第{index + 1}点") for index, value in enumerate(values)]
    hours = tuple(
        round(sum(numbers[hour * 4 : hour * 4 + 4]) / 4, 4)
        for hour in range(24)
    )
    return ForecastRow(
        forecast_date=date_text,
        daily_total=round(sum(numbers) / 4, 4),
        daily_average=round(sum(numbers) / len(numbers), 4),
        hours=hours,
    )


def average_of_24_hours(row: ForecastRow) -> float:
    if len(row.hours) != 24:
        raise ValueError("计算日平均时必须有24个小时值")
    return sum(row.hours) / 24


def format_shifted_value(value: float) -> str:
    if value == 0:
        return "0"
    shifted = Decimal(str(value)) / Decimal("10")
    rounded = shifted.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return format(rounded, ".0f")


def select_seven_rows(raw_rows: Any) -> list[ForecastRow]:
    if raw_rows is None or raw_rows == []:
        raise ForecastDataUnavailable("接口未返回风力预测记录，无法生成截图。请更换运行日期或稍后重试。")
    if not isinstance(raw_rows, list):
        raise RuntimeError("风力预测接口返回格式不正确：data 不是列表")
    by_date: dict[str, ForecastRow] = {}
    errors: list[str] = []
    for item in raw_rows:
        if not isinstance(item, dict) or not str(item.get("date") or "")[:10]:
            continue
        try:
            row = build_forecast_row(item)
        except (RuntimeError, ValueError) as exc:
            errors.append(str(exc))
            continue
        by_date[row.forecast_date] = row

    rows = [by_date[key] for key in sorted(by_date)[:EXPECTED_DAY_COUNT]]
    if len(rows) != EXPECTED_DAY_COUNT:
        detail = f"；数据不完整的原因：{errors[0]}" if errors else ""
        raise ForecastDataUnavailable(f"接口中只有 {len(rows)} 个完整预测日期，生成截图需要连续7天完整数据{detail}")

    dates = [datetime.strptime(row.forecast_date, "%Y-%m-%d").date() for row in rows]
    for previous, current in zip(dates, dates[1:]):
        if current != previous + timedelta(days=1):
            raise ForecastDataUnavailable(f"预测日期不连续：{previous.isoformat()} 后为 {current.isoformat()}，生成截图需要连续7天完整数据。")
    return rows


def fetch_forecast_rows(context, run_date: str, config: Config, auth) -> list[ForecastRow]:
    log(f"正在切换公司：{config.org_name}")
    auth.switch_company(context, config.org_id)
    log(f"正在读取 {config.unit_name} 的风力预测：运行日 {run_date}")
    payload = auth.request_json_with_retry(
        context,
        "post",
        API_URL,
        f"读取风力预测 {run_date}",
        data={
            "runDate": run_date,
            "orgIds": [config.org_id],
            "unitIds": [config.unit_id],
        },
        timeout=60000,
    )
    if "data" not in payload:
        raise RuntimeError("风力预测接口返回格式不正确：缺少 data 字段")
    return select_seven_rows(payload["data"])


def _font(size: int, *, bold: bool = False):
    if sys.platform == "darwin":
        candidates = (
            ("/System/Library/Fonts/Supplemental/Songti.ttc", 0 if bold else 6),
            ("/System/Library/Fonts/PingFang.ttc", 0),
            ("/System/Library/Fonts/STHeiti Medium.ttc" if bold else
             "/System/Library/Fonts/STHeiti Light.ttc", 0),
            ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0),
        )
        for path, index in candidates:
            try:
                return ImageFont.truetype(path, size=size, index=index)
            except OSError:
                continue
    # Preserve Chinese glyphs and the requested size if system fonts are unavailable.
    path = SCRIPT_DIR / "fonts" / "NotoSerifSC.ttf"
    try:
        font = ImageFont.truetype(str(path), size=size)
        font.set_variation_by_axes([700 if bold else 400])
        return font
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"截图中文字体加载失败：{path}。请重新解压完整程序或字体修复补丁。") from exc


def render_table_png(
    rows: list[ForecastRow],
    output_path: Path,
    *,
    org_name: str = DEFAULT_ORG_NAME,
    unit_name: str = DEFAULT_UNIT_NAME,
) -> Path:
    if len(rows) != EXPECTED_DAY_COUNT:
        raise ValueError("截图必须恰好包含7个预测日期")
    del org_name, unit_name
    date_headers = []
    for row in rows:
        forecast_date = datetime.strptime(row.forecast_date, "%Y-%m-%d")
        date_headers.append(f"{forecast_date.month} 月 {forecast_date.day} 日")

    first_column_width = 300
    date_column_width = 220
    header_height = 100
    total_height = 150
    average_height = 96
    hour_height = 68
    width = first_column_width + date_column_width * len(rows) + 2
    height = header_height + total_height + average_height + hour_height * 24 + 2
    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)
    header_font = _font(46, bold=True)
    label_font = _font(44, bold=True)
    body_font = _font(48, bold=True)
    total_font = _font(48, bold=True)
    border = "#151515"

    def draw_cell(
        x: int,
        y: int,
        cell_width: int,
        cell_height: int,
        text: str,
        *,
        fill: str = "#ffffff",
        font=None,
    ) -> None:
        font = font or body_font
        draw.rectangle((x, y, x + cell_width, y + cell_height), fill=fill, outline=border, width=2)
        box = draw.multiline_textbbox(
            (0, 0), text, font=font, spacing=4, align="center", stroke_width=1
        )
        text_width = box[2] - box[0]
        text_height = box[3] - box[1]
        # CJK fonts have different metrics; keep two-digit dates and long
        # totals inside their cells while retaining the original body size.
        while font.size > 1 and (text_width > cell_width - 20 or text_height > cell_height - 12):
            scale = min((cell_width - 20) / text_width, (cell_height - 12) / text_height)
            font = _font(max(1, min(font.size - 1, int(font.size * scale))), bold=True)
            box = draw.multiline_textbbox(
                (0, 0), text, font=font, spacing=4, align="center", stroke_width=1
            )
            text_width = box[2] - box[0]
            text_height = box[3] - box[1]
        draw.multiline_text(
            (
                x + (cell_width - text_width) / 2 - box[0],
                y + (cell_height - text_height) / 2 - box[1],
            ),
            text,
            font=font,
            fill="#111111",
            stroke_width=1,
            stroke_fill="#111111",
            spacing=4,
            align="center",
        )

    header_fill = "#dce6f7"
    total_fill = "#eeecef"
    draw_cell(1, 1, first_column_width, header_height, "日期", fill=header_fill, font=header_font)
    for column, header in enumerate(date_headers):
        draw_cell(
            1 + first_column_width + column * date_column_width,
            1,
            date_column_width,
            header_height,
            header,
            fill=header_fill,
            font=header_font,
        )

    total_y = 1 + header_height
    draw_cell(1, total_y, first_column_width, total_height, "日合计\n（万千瓦时）", fill=total_fill, font=label_font)
    for column, row in enumerate(rows):
        draw_cell(
            1 + first_column_width + column * date_column_width,
            total_y,
            date_column_width,
            total_height,
            format_shifted_value(row.daily_total),
            fill=total_fill,
            font=total_font,
        )

    average_y = total_y + total_height
    draw_cell(1, average_y, first_column_width, average_height, "日平均", fill=total_fill, font=label_font)
    for column, row in enumerate(rows):
        draw_cell(
            1 + first_column_width + column * date_column_width,
            average_y,
            date_column_width,
            average_height,
            format_shifted_value(average_of_24_hours(row)),
            fill=total_fill,
            font=total_font,
        )

    for hour in range(24):
        y = average_y + average_height + hour * hour_height
        draw_cell(1, y, first_column_width, hour_height, f"{hour}:00", font=label_font)
        for column, row in enumerate(rows):
            draw_cell(
                1 + first_column_width + column * date_column_width,
                y,
                date_column_width,
                hour_height,
                format_shifted_value(row.hours[hour]),
                font=body_font,
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG")
    return output_path


def output_path_for(rows: list[ForecastRow], output_dir: Path) -> Path:
    return output_dir / f"风力预测_{rows[0].forecast_date.replace('-', '')}_至_{rows[-1].forecast_date.replace('-', '')}.png"


def generate_screenshot(run_date: str, config: Config | None = None) -> Path:
    run_date = validate_date(run_date)
    config = config or load_config()
    auth = load_auth_module()
    with auth.AuthStateLock():
        with sync_playwright() as playwright:
            context = auth.launch_context(playwright, headless=config.headless)
            try:
                auth.ensure_login(context, config)
                rows = fetch_forecast_rows(context, run_date, config, auth)
            finally:
                context.close()
    output = render_table_png(
        rows,
        output_path_for(rows, config.output_dir),
        org_name=config.org_name,
        unit_name=config.unit_name,
    )
    log(f"已生成7天风力预测截图：{output}")
    return output


def interactive_login(config: Config) -> None:
    standalone_login(load_auth_module(), config, app_url=APP_URL, log=log)


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取7天风力预测并生成日期横向、小时纵向的表格 PNG")
    parser.add_argument("date", nargs="?", help="运行日，格式 YYYY-MM-DD")
    parser.add_argument("--login", action="store_true", help="登录并保存本模块独立会话")
    args = parser.parse_args()
    config = load_config()
    try:
        if args.login:
            interactive_login(config)
            return 0
        run_date = validate_date(args.date or datetime.now().strftime("%Y-%m-%d"))
        generate_screenshot(run_date, config)
        return 0
    except ForecastDataUnavailable as exc:
        log(f"无数据：运行日期 {run_date}。原因：{exc}")
        return 0
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130
    except Exception as exc:
        from toolbox_shared.cli_login import emit_login_error
        emit_login_error(exc)
        print(f"失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
