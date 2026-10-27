"""Shared WPS paths and runtime limits; profile remains isolated from other tools."""
from pathlib import Path
import threading
from .storage import WORKSPACE_ROOT, data_path

BASE_DIR = WORKSPACE_ROOT / "wps自动"
DATA_DIR = data_path(BASE_DIR)
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = DATA_DIR / "wps_excel_to_kdocs_config.json"
DEFAULT_CONFIG_PATH = BASE_DIR / "wps_excel_to_kdocs_config.defaults.json"
PROFILE_DIR = DATA_DIR / "wps-browser-profile"
WPS_CHROME_PID_PATH = DATA_DIR / "wps-chrome.pid"
WPS_CHROME_STATE_PATH = DATA_DIR / "wps-chrome-state.json"
WPS_CHROME_LAUNCH_VERSION = 6
WPS_CHROME_START_LOCK = threading.Lock()
LOG_DIR = DATA_DIR / "logs"
MAX_RECENT_URLS = 10
DOCUMENT_CONFIGS_MIN_HEIGHT = 220
CDP_CONNECT_TIMEOUT_MS = 15000
WPS_LOGIN_STATUS_URL = "https://account.kdocs.cn/api/v3/islogin"
LOGIN_STATUS_POLL_MS = 15000
