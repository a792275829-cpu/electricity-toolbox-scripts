"""Market workbook contract: exact historical names, ranges and field order.

This module performs no login, GUI creation, file access or dynamic imports.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from types import MappingProxyType

MARKET_SHEET_NAME = "2026年市场情况"
COST_SHEET_NAME = "2026年运行方式及成本"
DAY_AHEAD_LOAD_START_COLUMN = "B"
DAY_AHEAD_LOAD_END_COLUMN = "U"
DAY_AHEAD_MARKET_START_COLUMN = "V"
DAY_AHEAD_MARKET_END_COLUMN = "AG"
ACTUAL_LOAD_START_COLUMN = "AH"
ACTUAL_LOAD_END_COLUMN = "BA"
REALTIME_MARKET_START_COLUMN = "BB"
REALTIME_MARKET_END_COLUMN = "BE"
GENERATION_SETTLEMENT_START_COLUMN = "BF"
GENERATION_SETTLEMENT_END_COLUMN = "BQ"
USER_SETTLEMENT_START_COLUMN = "BR"
USER_SETTLEMENT_END_COLUMN = "BX"
UNIT_COST_START_COLUMN = "N"
UNIT_COST_END_COLUMN = "X"
OPERATION_MODE_START_COLUMN = "B"
OPERATION_MODE_END_COLUMN = "M"
MARKET_BOARD_ONLINE_UNIT_COLUMNS = ("AA", "AE")
SPOT_CLEARING_UNIT_COUNT_START_COLUMN = "AB"
SPOT_CLEARING_UNIT_COUNT_END_COLUMN = "AC"
DAY_AHEAD_CLEARING_UNIT_COUNT_GROUPS = (
    "DAY_AHEAD_CLEARANCE_COAL",
    "DAY_AHEAD_CLEARANCE_GAS",
)
ACTUAL_LOAD_TYPES = (
    "1",  # 统调
    "6",  # B类
    "4",  # 西电
    "2",  # A类
    "3",  # 地方电
)
DEFAULT_POINT = 96
DEFAULT_PROVINCE_AREA_ID = "044"
UNIT_COST_ORG_IDS = (
    "e4e6eb5c80731ac70180fab1ba2f0559",  # 汕头电厂
    "e4e6eb5c80731ac70180fab3532d0592",  # 海门电厂
    "e4e6eb5c80731ac70180faa7f96904eb",  # 谢岗电厂
)
UNIT_COST_PRICE_UNIT_IDS = (
    "e4e6eb5580fa3aa00180fbcb51be0016",  # 汕头#1机组
    "e4e6eb5580fa3aa00180fbcb69a40017",  # 汕头#2机组
    "e4e6eb5580fa3aa00180fbcb89040018",  # 汕头#3机组
    "e4e6eb5580fa3aa00180fbbf9e940007",  # 海门#1机组
    "e4e6eb5580fa3aa00180fbc0d3800008",  # 海门#2机组
    "e4e6eb5580fa3aa00180fbc0f8690009",  # 海门#3机组
    "e4e6eb5580fa3aa00180fbc1276a000a",  # 海门#4机组
    "e4e6eb5580fa3aa00180fbd3ff800027",  # 谢岗#1、#2机组
    "e4e6eb5580fa3aa00180fbd457c40028",  # 谢岗#3、#4机组
    "e4e3842b93f2cbd90193f6af88e70796",  # 谢岗#5、#6机组
    "e4e3842b93f2cbd90193f6b01de40797",  # 谢岗#7、#8机组
)
DAY_AHEAD_MARKET_FIELDS = (
    "generateSideDeclareAvgPrice",
    "totalGenerateSideDealEle",
    "totalCoalDealEle",
    "totalGasDealEle",
    "totalNuclearDealEle",
    "totalNewEnergyDealEle",
    "totalPowerSideDealEle",
    "generateSideAvgPrice",
    "coalDealMaxPrice",
    "coalDealAvgPrice",
    "gasDealMaxPrice",
    "gasDealAvgPrice",
)
REALTIME_MARKET_FIELDS = (
    ("generateSideDealEle", "totalGenerateSideDealEle"),
    "generateSideAvgPrice",
    "coalDealAvgPrice",
    "gasDealAvgPrice",
)
GENERATION_SETTLEMENT_FIELDS = (
    "marketUnitOnlineEle",
    "marketUnitBaseEle",
    "marketUnitBaseEleRadio",
    "marketUnitMltEle",
    "marketUnitMltEleRadio",
    "marketUnitDeviationEle",
    "marketUnitDeviationEleRadio",
    "spotPosDeviationRadio",
    "spotNegDeviationRadio",
    "marketUnitFee",
    "generateAvgPriceWithCompensate",
    "generateAvgPrice",
)
GENERATION_SETTLEMENT_PERCENT_FIELDS = {
    "marketUnitBaseEleRadio",
    "marketUnitMltEleRadio",
    "marketUnitDeviationEleRadio",
    "spotPosDeviationRadio",
    "spotNegDeviationRadio",
}
USER_SETTLEMENT_FIELDS = (
    "marketUserConsumeEle",
    "mltEle",
    "mltEleRadio",
    "spotDeviationEle",
    "spotDeviationRadio",
    "getMarketUserConsumeFee",
    "userAvePrice",
)
USER_SETTLEMENT_PERCENT_FIELDS = {
    "mltEleRadio",
    "spotDeviationRadio",
}


@dataclass(frozen=True)
class MarketSeriesContract:
    source_days: int
    sheet: str
    first_column: str
    last_column: str
    fields: tuple = ()
    percent_fields: frozenset[str] = frozenset()

    def source_date(self, base_date: date) -> date:
        return base_date + timedelta(days=self.source_days)


MARKET_SERIES = MappingProxyType({
    "day_ahead_load": MarketSeriesContract(0, MARKET_SHEET_NAME, DAY_AHEAD_LOAD_START_COLUMN, DAY_AHEAD_LOAD_END_COLUMN),
    "actual_load": MarketSeriesContract(-2, MARKET_SHEET_NAME, ACTUAL_LOAD_START_COLUMN, ACTUAL_LOAD_END_COLUMN),
    "day_ahead_market": MarketSeriesContract(-1, MARKET_SHEET_NAME, DAY_AHEAD_MARKET_START_COLUMN, DAY_AHEAD_MARKET_END_COLUMN, DAY_AHEAD_MARKET_FIELDS),
    "realtime_market": MarketSeriesContract(-2, MARKET_SHEET_NAME, REALTIME_MARKET_START_COLUMN, REALTIME_MARKET_END_COLUMN, REALTIME_MARKET_FIELDS),
    "generation_settlement": MarketSeriesContract(-6, MARKET_SHEET_NAME, GENERATION_SETTLEMENT_START_COLUMN, GENERATION_SETTLEMENT_END_COLUMN, GENERATION_SETTLEMENT_FIELDS, frozenset(GENERATION_SETTLEMENT_PERCENT_FIELDS)),
    "user_settlement": MarketSeriesContract(-6, MARKET_SHEET_NAME, USER_SETTLEMENT_START_COLUMN, USER_SETTLEMENT_END_COLUMN, USER_SETTLEMENT_FIELDS, frozenset(USER_SETTLEMENT_PERCENT_FIELDS)),
})
