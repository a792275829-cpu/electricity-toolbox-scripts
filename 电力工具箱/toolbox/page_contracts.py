"""Explicit UI-to-service contracts, independent of page names and copy."""
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class PageBinding:
    tool_id: str
    fields: tuple[str, ...]


PAGE_BINDINGS = MappingProxyType({
    'online-energy': PageBinding('online-energy', ('run_date',)),
    'wind-forecast': PageBinding('wind-forecast', ('run_date',)),
    'section-analysis': PageBinding('section-analysis', ('output_root', 'topology_path', 'threshold')),
    'trade-analysis': PageBinding('trade-analysis', ('output_dir',)),
    'market-table': PageBinding('market-table', ('run_date', 'workbook')),
    'transaction-info': PageBinding('transaction-info', ('workbook', 'start_date', 'end_date', 'create_backup')),
    'power-benefit': PageBinding('power-benefit', ('start_date', 'end_date', 'create_backup')),
    'spot-price': PageBinding('spot-price', ('start_date', 'end_date', 'overwrite', 'create_backup')),
    'daily-pdf-parser': PageBinding('daily-pdf-parser', ('pdf_path', 'output_path', 'mode')),
    'market-report': PageBinding('market-report', ('report_date', 'day_report', 'previous_report', 'd2_report', 'manual_market')),
    'report': PageBinding('report', ('report_date', 'online_workbook', 'day_ahead_workbook', 'daily_workbook', 'template_report', 'fetch_data', 'generate_word')),
    'guangdong-price': PageBinding('guangdong-price', ('run_date', 'master_workbook', 'prediction_output_dir', 'training_months')),
    'group-upload': PageBinding('group-upload', ('force',)),
    'summary-upload': PageBinding('summary-upload', ('input_dir', 'mode')),
})
