import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "find_output_range_deviation_days.py"
SPEC = importlib.util.spec_from_file_location("find_output_range_deviation_days", SCRIPT_PATH)
deviation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = deviation
SPEC.loader.exec_module(deviation)


def make_row(market_type, date, unit, time, deviation_mw, clearing_mwh=100.0):
    return deviation.HourDeviation(
        market_type=market_type,
        date=date,
        unit=unit,
        time=time,
        clearing_mwh=clearing_mwh,
        clearing_price=300.0,
        plant_output=clearing_mwh,
        normal_range="-",
        deviation_mw=deviation_mw,
        source_file=f"{date}.xlsx",
    )


def test_output_range_distance_uses_nearest_boundary():
    bounded = deviation.NormalOutputRange(lower=100.0, upper=200.0, label="100.00-200.00MW")
    open_ended = deviation.NormalOutputRange(lower=250.0, upper=None, label=">=250.00MW")

    assert deviation.output_range_distance(bounded, 150.0) == 0.0
    assert deviation.output_range_distance(bounded, 80.0) == 20.0
    assert deviation.output_range_distance(bounded, 240.0) == 40.0
    assert deviation.output_range_distance(open_ended, 200.0) == 50.0
    assert deviation.output_range_distance(open_ended, 300.0) == 0.0


def test_rank_plant_days_sums_all_units_by_market_date():
    rows = [
        make_row("日前", "2026-06-01", "海门#1机组", "0:00", 10.0),
        make_row("日前", "2026-06-01", "海门#2机组", "0:00", 20.0),
        make_row("日前", "2026-06-02", "海门#1机组", "0:00", 5.0),
        make_row("实时", "2026-06-01", "海门#1机组", "0:00", 30.0),
        make_row("实时", "2026-06-02", "海门#1机组", "0:00", 7.0),
    ]

    ranked = deviation.rank_plant_days(rows, top=1)

    assert [(item.market_type, item.date, item.total_deviation_mw) for item in ranked] == [
        ("日前", "2026-06-01", 30.0),
        ("实时", "2026-06-01", 30.0),
    ]
    assert ranked[0].unit_count == 2


def test_rank_plant_days_excludes_start_stop_dates():
    rows = [
        make_row("日前", "2026-06-01", "海门#1机组", "0:00", 99.0, clearing_mwh=0.0),
        make_row("日前", "2026-06-01", "海门#1机组", "1:00", 99.0, clearing_mwh=100.0),
        make_row("日前", "2026-06-02", "海门#1机组", "0:00", 10.0, clearing_mwh=100.0),
        make_row("实时", "2026-06-03", "海门#1机组", "0:00", 8.0, clearing_mwh=0.0),
        make_row("实时", "2026-06-03", "海门#1机组", "1:00", 7.0, clearing_mwh=0.0),
    ]

    ranked, excluded = deviation.rank_plant_days_with_exclusions(rows, top=10)

    assert [(item.market_type, item.date) for item in ranked] == [
        ("日前", "2026-06-02"),
        ("实时", "2026-06-03"),
    ]
    assert excluded == [("2026-06-01", "存在机组出力状态变化")]


def test_top_per_market_keeps_each_market_separate():
    scores = [
        deviation.PlantDayScore("日前", "2026-06-01", 10.0, 1, 1, "a.xlsx"),
        deviation.PlantDayScore("日前", "2026-06-02", 9.0, 1, 1, "b.xlsx"),
        deviation.PlantDayScore("实时", "2026-06-01", 8.0, 1, 1, "a.xlsx"),
        deviation.PlantDayScore("实时", "2026-06-02", 7.0, 1, 1, "b.xlsx"),
    ]

    limited = deviation.top_per_market(scores, top=1)

    assert [(score.market_type, score.date) for score in limited] == [
        ("日前", "2026-06-01"),
        ("实时", "2026-06-01"),
    ]
