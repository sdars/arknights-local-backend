import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
BASE_DIR = APP_DIR.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.environ.get("ARK_DB", DATA_DIR / "arknights.sqlite3"))
PLAYER_TEMPLATE = DATA_DIR / "player_template.json"
BATTLE_START_TEMPLATE = BASE_DIR / "fixtures" / "battle_start_guide01.json"

SECRET_KEY = os.environ.get("ARK_SECRET_KEY", "change-this-session-secret-in-production")
ADMIN_USER = os.environ.get("ARK_ADMIN_USER", "sdars")
ADMIN_PASSWORD = os.environ.get("ARK_ADMIN_PASSWORD", "123456")

SERVER_NAME = os.environ.get("ARK_SERVER_NAME", "泰拉")
CLIENT_VERSION = os.environ.get("ARK_CLIENT_VERSION", "2.7.91")
RES_VERSION = os.environ.get("ARK_RES_VERSION", "26-11-13-06-37-23-89286612")
SERVER_URL = os.environ.get("ARK_SERVER_URL", "http://127.0.0.1:8443")
ASSET_CDN = os.environ.get("ARK_ASSET_CDN", "https://ak.hycdn.cn/assetbundle/official")

APP_CODE = "7318def77669979d"
CONFIG_VER = "5"
FUNC_VER = "V059"

