#!/usr/bin/env python3
"""Report-local entry point for the shared online-energy exporter.

The report keeps its own config, output, browser profile, cookies and lock.
Business data collection and workbook formatting live in one implementation.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
APP_ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name in {"scripts", "脚本"} else SCRIPT_DIR
WORKSPACE = APP_ROOT.parent
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

from toolbox_auth_loader import load_login_module
from toolbox_credentials import read_login_config


from toolbox_storage import data_path

CONFIG_PATH = data_path(SCRIPT_DIR / "config.json")
OUTPUT_DIR = data_path(APP_ROOT / "输出")
PROFILE_DIR = data_path(SCRIPT_DIR / ".browser-profile")
AUTH_STATE_PATH = data_path(SCRIPT_DIR / "auth_state.json")
AUTH_LOCK_PATH = data_path(SCRIPT_DIR / "auth_state.json.lock")

_export = load_login_module(
    WORKSPACE,
    module_name="report_online_energy_core",
    state_dir=SCRIPT_DIR,
    config_path=CONFIG_PATH,
)
_export.OUTPUT_DIR = OUTPUT_DIR

# Keep the names used by the report's other scripts and existing callers.
BASE_URL = _export.BASE_URL
FIRE_APP_URL = _export.FIRE_APP_URL
ENERGY_APP_URL = _export.ENERGY_APP_URL
LOGIN_URL = _export.LOGIN_URL
FALLBACK_COMPANIES = _export.FALLBACK_COMPANIES
COMPANY_DISPLAY_NAME = _export.COMPANY_DISPLAY_NAME
COMPANY_DISPLAY_ORDER = _export.COMPANY_DISPLAY_ORDER
AuthStateLock = _export.AuthStateLock
log = _export.log
validate_date = _export.validate_date
ask_date = _export.ask_date
parse_json_response = _export.parse_json_response
request_json_with_retry = _export.request_json_with_retry
json_or_text = _export.json_or_text
load_saved_auth_state = _export.load_saved_auth_state
save_auth_state = _export.save_auth_state
playwright_proxy = _export.playwright_proxy
launch_context = _export.launch_context
is_logged_in = _export.is_logged_in
base64_decode = _export.base64_decode
base64_encode = _export.base64_encode
rsa_encrypt_password = _export.rsa_encrypt_password
try_api_login = _export.try_api_login
try_ui_login = _export.try_ui_login
try_auto_login = _export.try_auto_login
interactive_login = _export.interactive_login
ensure_login = _export.ensure_login
get_companies = _export.get_companies
switch_company = _export.switch_company
get_units = _export.get_units
unit_display_name = _export.unit_display_name
natural_key = _export.natural_key
is_renewable_unit = _export.is_renewable_unit
is_renewable_company = _export.is_renewable_company
number_or_zero = _export.number_or_zero
fetch_fire_rows = _export.fetch_fire_rows
fetch_energy_rows = _export.fetch_energy_rows
collect_detail_rows = _export.collect_detail_rows
display_unit_header = _export.display_unit_header
display_company_name = _export.display_company_name
build_company_blocks = _export.build_company_blocks
write_summary_sheet = _export.write_summary_sheet
save_excel = _export.save_excel
export_online_energy = _export.export_online_energy


@dataclass
class Config:
    username: str = ""
    password: str = ""
    headless: bool = True
    request_delay_seconds: float = 1.0
    output_dir: Path = OUTPUT_DIR


def load_config() -> Config:
    raw = read_login_config(CONFIG_PATH)
    return Config(
        username=str(raw.get("username", "") or ""),
        password=str(raw.get("password", "") or ""),
        headless=bool(raw.get("headless", True)),
        request_delay_seconds=float(raw.get("requestDelaySeconds", 1.0)),
        output_dir=data_path(Path(raw.get("outputDir", str(OUTPUT_DIR))).expanduser()),
    )


_export.load_config = load_config
main = _export.main


if __name__ == "__main__":
    raise SystemExit(main())
