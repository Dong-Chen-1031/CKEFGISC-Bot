import os
import sys
from dotenv import load_dotenv

# 載入 .env 檔案中的環境變數
load_dotenv()

# Discord 機器人設定檔案

# Discord 機器人 Token
DISCORD_BOT_TOKEN = str(os.getenv("DISCORD_BOT_TOKEN"))

AUTO_RELOAD = False # 是否自動重新載入 Cogs

# 開發者指令的開發者ID
DEV_ID = [
    123456789012345678,  # 替換為你的 Discord 用戶 ID
    987654321098765432   # 可以添加多個開發者 ID
]

# 檢查 Token 是否存在
if DISCORD_BOT_TOKEN is None:
    print("錯誤: 找不到 DISCORD_BOT_TOKEN 環境變數")
    print("請確認 .env 檔案中已正確設置 DISCORD_BOT_TOKEN")
    print("格式應為: DISCORD_BOT_TOKEN = \"你的Token\"")
    sys.exit(1)

# 機器人開發者指令前綴
PREFIX = ".dev "

# ── 倒數頻道設定 ──

# 計算倒數用的時區
TIMEZONE = "Asia/Taipei"

# 每幾分鐘檢查一次倒數頻道名稱
# Discord 限制每個頻道 10 分鐘只能改名 2 次，不建議調得比 5 更低
COUNTDOWN_UPDATE_INTERVAL = 10

# ── 資料庫 ──

# SQLite 資料庫位置
DATABASE_FILE = "data/bot.db"

# 舊版 JSON 的位置。資料庫是空的、而這些檔案還在的話，
# 開機時會自動把資料搬進資料庫（只會搬一次）。
COUNTDOWN_DATA_FILE = "data/countdowns.json"
NOTIFY_DATA_FILE = "data/notifications.json"

# ── 群發通知設定 ──


def _env_bool(key: str, default: bool) -> bool:
    """讀取 .env 裡的布林開關，接受 true/1/yes/on 這類寫法"""
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "y")


# /notify 是否需要「管理伺服器」權限。
# 設成 false 之後所有人都能使用 /notify，包含一次私訊整個身分組，
# 請確定你的伺服器適合這樣開放。改完要重啟機器人才會生效。
NOTIFY_REQUIRE_PERMISSION = _env_bool("NOTIFY_REQUIRE_PERMISSION", True)

# 是否允許匿名寄送。設成 false 之後 /notify send 的 anonymous 選項會被拒絕，
# 新發出的通知一律帶上發布者名稱與頭像。
# 已經發出去的舊通知不受影響（見 README 說明）。改完要重啟機器人才會生效。
NOTIFY_ALLOW_ANONYMOUS = _env_bool("NOTIFY_ALLOW_ANONYMOUS", True)

# ── 表情符號複製 ──

# 每個表符建立之間間隔幾秒。Discord 對建立表符的速率限制很緊，
# 調太低容易被 429 卡住整批作業。
EMOJI_COPY_DELAY = 1.5

# ── 群發通知設定（續）──

# 每封私訊之間間隔幾秒，太短會被 Discord 限流
NOTIFY_SEND_DELAY = 1.0