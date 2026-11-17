"""Validated three-day Guangzhou forecast with independent HTTPS sources."""
from __future__ import annotations

import json
import math
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from html.parser import HTMLParser

LATITUDE, LONGITUDE = 23.1291, 113.2644
CHINA_WEATHER_URL = "https://www.weather.com.cn/weather/101280101.shtml"


def weather_code_label(code):
    value = int(code)
    if value == 0:
        return "晴"
    for codes, label in (
        ({1, 2}, "多云"), ({3}, "阴"), ({45, 48}, "雾"),
        ({51, 53, 55, 56, 57, 61, 80}, "小雨"), ({63, 66, 81}, "中雨"),
        ({65, 67, 82}, "大雨"), ({71, 73, 75, 77, 85, 86}, "雪"),
        ({95, 96, 99}, "雷阵雨"),
    ):
        if value in codes:
            return label
    raise ValueError(f"未知天气代码：{code}")


class _ChinaForecast(HTMLParser):
    def __init__(self):
        super().__init__()
        self.published = None
        self.rows = []
        self.in_forecast = False
        self.row = None
        self.field = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()
        if tag == "input" and attrs.get("id") == "fc_24h_internal_update_time":
            self.published = datetime.strptime(attrs.get("value", ""), "%Y%m%d%H").date()
        if tag == "ul" and "t" in classes and "clearfix" in classes:
            self.in_forecast = True
        if self.in_forecast and tag == "li":
            self.row = {"day": "", "weather": "", "temperature": ""}
        if self.row is not None:
            if tag == "h1":
                self.field = "day"
            elif tag == "p":
                self.field = "weather" if "wea" in classes else "temperature" if "tem" in classes else None

    def handle_data(self, data):
        if self.row is not None and self.field:
            self.row[self.field] += data

    def handle_endtag(self, tag):
        if tag in {"h1", "p"}:
            self.field = None
        if tag == "li" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        if tag == "ul":
            self.in_forecast = False


def _temperature(value):
    if value is None or isinstance(value, bool):
        raise ValueError("气温缺失")
    result = float(value)
    if not math.isfinite(result) or not -90 <= result <= 65:
        raise ValueError("气温无效")
    return result


def parse_china_forecast(html: str):
    parser = _ChinaForecast()
    parser.feed(html)
    if parser.published is None:
        raise ValueError("中国天气网缺少预报发布日期")
    days = []
    for offset, row in enumerate(parser.rows):
        day = parser.published + timedelta(days=offset)
        if not re.match(rf"^{day.day}日", row["day"].strip()):
            raise ValueError("中国天气网预报日期与发布日期不一致")
        temperatures = re.fullmatch(r"\s*([-+]?\d+)\s*(?:℃)?\s*/\s*([-+]?\d+)\s*℃\s*", row["temperature"])
        if not temperatures or not row["weather"].strip():
            raise ValueError("中国天气网日间最高温、最低温或天气缺失")
        days.append(dict(date=day.isoformat(), weather=row["weather"].strip(),
                         maximum=_temperature(temperatures[1]), minimum=_temperature(temperatures[2])))
    return days


def parse_open_meteo(data):
    if not isinstance(data, dict) or not isinstance(data.get("daily"), dict):
        raise ValueError("Open-Meteo 返回内容不是预报对象")
    daily = data["daily"]
    fields = [daily.get(key) for key in ("time", "weather_code", "temperature_2m_max", "temperature_2m_min")]
    if any(not isinstance(items, list) for items in fields) or len({len(items) for items in fields}) != 1:
        raise ValueError("Open-Meteo 日期、天气和气温数组不完整")
    return [dict(date=day, weather=weather_code_label(code), maximum=_temperature(high), minimum=_temperature(low))
            for day, code, high, low in zip(*fields)]


def _select_days(days, report_date):
    start = date.fromisoformat(report_date)
    wanted = [(start + timedelta(days=offset)).isoformat() for offset in range(3)]
    by_date = {day["date"]: day for day in days}
    if len(by_date) != len(days) or any(day not in by_date for day in wanted):
        raise ValueError(f"天气预报未完整覆盖 {wanted[0]} 至 {wanted[-1]}，拒绝使用过期或缺日数据")
    selected = [by_date[day] for day in wanted]
    if any(day["minimum"] > day["maximum"] for day in selected):
        raise ValueError("天气预报最低温高于最高温")
    return selected


def fetch_guangdong_weather(report_date: str, *, log=print):
    start = date.fromisoformat(report_date)
    query = urllib.parse.urlencode(dict(latitude=LATITUDE, longitude=LONGITUDE,
        daily="weather_code,temperature_2m_max,temperature_2m_min", timezone="Asia/Shanghai",
        start_date=start.isoformat(), end_date=(start + timedelta(days=2)).isoformat()))
    sources = [("Open-Meteo", f"https://api.open-meteo.com/v1/forecast?{query}", 6),
               ("中国天气网", CHINA_WEATHER_URL, 8)]
    failures = []
    first_error = None
    for name, url, timeout in sources:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "ElectricityToolbox/1.0"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
            days = (parse_open_meteo(json.loads(raw)) if name == "Open-Meteo"
                    else parse_china_forecast(raw.decode("utf-8")))
            selected = _select_days(days, report_date)
            first, last = selected[0]["weather"], selected[-1]["weather"]
            weather = first if first == last else f"{first}转{last}"
            text = f"未来三天{weather}，气温{round(min(d['minimum'] for d in selected))}℃-{round(max(d['maximum'] for d in selected))}℃，"
            log(f"天气自动更新：{name}，广州，{selected[0]['date']} 至 {selected[-1]['date']}")
            return dict(text=text, source=name, url=url, location="广州", days=selected, failures=failures)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            if first_error is None:
                first_error = exc
            failures.append(dict(source=name, error=f"{type(exc).__name__}: {exc}"))
            log(f"天气来源 {name} 失败：{type(exc).__name__}: {exc}")
    detail = "；".join(f"{item['source']}：{item['error']}" for item in failures)
    raise RuntimeError(f"读取广东天气失败：{detail}") from first_error
