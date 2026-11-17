from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
from ssl import SSLCertVerificationError

from docx import Document
from toolbox_shared.morning_weather import fetch_guangdong_weather, parse_china_forecast
from generate_red_marked_report import replace_weather_prefix


def china_html(start="2026111708", temperatures=None):
    temperatures = temperatures or [(28, 20), (24, 14), (18, 13)]
    return (f'<input value="{start}" id="fc_24h_internal_update_time"><ul class="t clearfix">' +
            ''.join(f'<li class="sky"><h1>{17+i}日（预报）</h1><p class="wea">{label}</p>'
                    f'<p class="tem"><span>{high}</span>/<i>{low}℃</i></p></li>'
                    for i, ((high, low), label) in enumerate(zip(temperatures, ["多云", "小雨转阴", "阴"]))) + '</ul>')


def open_meteo():
    return dict(daily=dict(time=["2026-11-17", "2026-11-18", "2026-11-19"],
                          weather_code=[1, 61, 3], temperature_2m_max=[28, 24, 18],
                          temperature_2m_min=[20, 14, 13]))


def json_response(data):
    return io.BytesIO(json.dumps(data).encode())


class MorningWeatherTests(unittest.TestCase):
    def test_primary_success_preserves_location_and_date_contract(self):
        with patch("toolbox_shared.morning_weather.urllib.request.urlopen", return_value=json_response(open_meteo())) as request:
            result = fetch_guangdong_weather("2026-11-17", log=lambda _: None)
        self.assertEqual(result["text"], "未来三天多云转阴，气温13℃-28℃，")
        self.assertEqual(result["source"], "Open-Meteo")
        self.assertEqual(len(result["days"]), 3)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.kwargs["timeout"], 6)
        self.assertIn("start_date=2026-11-17", request.call_args.args[0].full_url)
        self.assertIn("end_date=2026-11-19", request.call_args.args[0].full_url)
        self.assertIn("latitude=23.1291", request.call_args.args[0].full_url)

    def test_tls_timeout_switches_source_and_records_first_failure(self):
        error = URLError(TimeoutError("_ssl.c:999: The handshake operation timed out"))
        with patch("toolbox_shared.morning_weather.urllib.request.urlopen", side_effect=[error, io.BytesIO(china_html().encode())]) as request:
            result = fetch_guangdong_weather("2026-11-17", log=lambda _: None)
        self.assertEqual(result["source"], "中国天气网")
        self.assertEqual(result["text"], "未来三天多云转阴，气温13℃-28℃，")
        self.assertIn("handshake", result["failures"][0]["error"])
        self.assertEqual([c.kwargs["timeout"] for c in request.call_args_list], [6, 8])
        self.assertTrue(request.call_args.args[0].full_url.startswith("https://www.weather.com.cn/"))
        self.assertNotIn("context", request.call_args.kwargs)

    def test_invalid_primary_data_uses_backup(self):
        for data in ([], {}, {"daily": {"time": []}}, {"daily": dict(open_meteo()["daily"], temperature_2m_min=[None]*3)},
                     {"daily": dict(open_meteo()["daily"], weather_code=[-1]*3)},
                     {"daily": dict(open_meteo()["daily"], time=["2026-11-16", "2026-11-17", "2026-11-18"])}):
            with self.subTest(data=data), patch("toolbox_shared.morning_weather.urllib.request.urlopen", side_effect=[json_response(data), io.BytesIO(china_html().encode())]):
                self.assertEqual(fetch_guangdong_weather("2026-11-17", log=lambda _: None)["source"], "中国天气网")

    def test_stale_or_incomplete_backup_is_rejected(self):
        for html in (china_html(), china_html().replace('19日', '20日'),
                     china_html().replace('<span>28</span>/', ''), china_html(start=""),
                     china_html(temperatures=[(20, 28), (24, 14), (18, 13)])):
            with self.subTest(html=html), patch("toolbox_shared.morning_weather.urllib.request.urlopen", side_effect=[URLError("offline"), io.BytesIO(html.encode())]):
                # Even the complete sample is stale for a report starting on the 18th.
                with self.assertRaisesRegex(RuntimeError, "读取广东天气失败"):
                    fetch_guangdong_weather("2026-11-18", log=lambda _: None)

    def test_inverted_temperatures_are_rejected_for_matching_dates(self):
        html = china_html(temperatures=[(20, 28), (24, 14), (18, 13)])
        with patch("toolbox_shared.morning_weather.urllib.request.urlopen", side_effect=[URLError("offline"), io.BytesIO(html.encode())]):
            with self.assertRaisesRegex(RuntimeError, "最低温高于最高温"):
                fetch_guangdong_weather("2026-11-17", log=lambda _: None)

    def test_no_temperature_zero_fill(self):
        with self.assertRaisesRegex(ValueError, "最高温"):
            parse_china_forecast(china_html().replace('<span>28</span>', '<span>/</span>'))

    def test_both_failures_preserve_first_cause_without_disabling_tls(self):
        first = URLError(TimeoutError("handshake timed out"))
        with patch("toolbox_shared.morning_weather.urllib.request.urlopen", side_effect=[first, SSLCertVerificationError("certificate expired")]) as request:
            with self.assertRaises(RuntimeError) as caught:
                fetch_guangdong_weather("2026-11-17", log=lambda _: None)
        self.assertIs(caught.exception.__cause__, first)
        self.assertIn("Open-Meteo", str(caught.exception))
        self.assertIn("中国天气网", str(caught.exception))
        self.assertEqual(request.call_count, 2)

    def test_year_boundary_uses_publication_date(self):
        html = china_html(start="2026123108").replace('17日', '31日').replace('18日', '1日').replace('19日', '2日')
        days = parse_china_forecast(html)
        self.assertEqual([d["date"] for d in days], ["2026-12-31", "2027-01-01", "2027-01-02"])

    def test_word_prefix_roundtrip_preserves_manual_forecast(self):
        result = dict(text="未来三天多云转阴，气温13℃-28℃，", source="中国天气网")
        for prefix in ("未来三天晴，气温20℃-30℃，", "未来三天晴，气温于20-30℃之间波动；"):
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as directory:
                doc = Document()
                paragraph = doc.add_paragraph(prefix + "预计统调最高负荷12800万千瓦。")
                with patch("generate_red_marked_report.fetch_guangdong_weather", return_value=result):
                    source = replace_weather_prefix(paragraph, "2026-11-17")
                self.assertEqual(source, result)
                path = Path(directory) / "weather.docx"
                doc.save(path)
                self.assertEqual(Document(path).paragraphs[0].text, result["text"] + "预计统调最高负荷12800万千瓦。")

    def test_failed_fetch_leaves_paragraph_unchanged(self):
        paragraph = Document().add_paragraph("未来三天晴，气温20℃-30℃，手写预测。")
        original = paragraph._p.xml
        with patch("generate_red_marked_report.fetch_guangdong_weather", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                replace_weather_prefix(paragraph, "2026-11-17")
        self.assertEqual(paragraph._p.xml, original)
