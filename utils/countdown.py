"""日期倒數的資料模型與儲存

倒數資料以 JSON 存在 `settings.COUNTDOWN_DATA_FILE`，以頻道 ID 為 key，
所以一個語音頻道同時只會有一個倒數。
"""

import datetime
import json
import logging
import os
from dataclasses import dataclass, asdict, field
from zoneinfo import ZoneInfo

import settings

TZ = ZoneInfo(settings.TIMEZONE)

DEFAULT_TEMPLATE = "{name} 剩 {days} 天"
DEFAULT_EXPIRED_TEMPLATE = "{name} 時間到"

# 頻道名稱長度上限
NAME_LIMIT = 100

# 可用於名稱模板的變數說明 (變數 -> 說明)
PLACEHOLDERS = {
    "{name}": "倒數的名稱",
    "{days}": "剩餘天數 (以日曆日計算，今天到期為 0)",
    "{d}": "剩餘完整天數 (精確到秒)",
    "{h}": "扣掉天數後的剩餘小時",
    "{m}": "扣掉天數、小時後的剩餘分鐘",
    "{total_days}": "剩餘總天數",
    "{total_hours}": "剩餘總小時",
    "{total_minutes}": "剩餘總分鐘",
    "{target}": "目標時間 (2025-01-01 08:00)",
    "{target_date}": "目標日期 (2025-01-01)",
    "{target_time}": "目標時刻 (08:00)",
}

_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%m-%d", "%m/%d", "%m.%d")
_TIME_FORMATS = ("%H:%M:%S", "%H:%M", "%H")


class _SafeDict(dict):
    """找不到的變數就原樣保留，避免使用者打錯字直接炸掉"""

    def __missing__(self, key):
        return "{" + key + "}"


def now() -> datetime.datetime:
    return datetime.datetime.now(TZ)


def parse_target(date_str: str, time_str: str | None = None) -> datetime.datetime:
    """把使用者輸入的日期(、時間)字串轉成帶時區的 datetime

    接受 2025-01-01、2025/1/1、1/1 (視為今年)；時間可寫在 date_str 裡或分開給。
    """
    date_str = date_str.strip()

    # 允許把時間直接打在日期欄位，例如 "2025-01-01 08:00"
    if " " in date_str:
        date_str, _, rest = date_str.partition(" ")
        rest = rest.strip()
        if rest and not time_str:
            time_str = rest

    date_part = None
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.datetime.strptime(date_str, fmt)
        except ValueError:
            continue
        if "%Y" in fmt:
            date_part = parsed.date()
        else:
            # 沒給年份 → 當作今年
            date_part = parsed.date().replace(year=now().year)
        break

    if date_part is None:
        raise ValueError(f"看不懂的日期格式：`{date_str}`，請用像 `2025-01-01` 或 `1/1` 的寫法")

    time_part = datetime.time(0, 0)
    if time_str:
        time_str = time_str.strip()
        for fmt in _TIME_FORMATS:
            try:
                time_part = datetime.datetime.strptime(time_str, fmt).time()
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"看不懂的時間格式：`{time_str}`，請用像 `08:00` 的寫法")

    return datetime.datetime.combine(date_part, time_part, tzinfo=TZ)


@dataclass
class Countdown:
    guild_id: int
    channel_id: int
    name: str
    target: str  # ISO 8601 字串
    template: str = DEFAULT_TEMPLATE
    expired_template: str = DEFAULT_EXPIRED_TEMPLATE

    @property
    def target_dt(self) -> datetime.datetime:
        dt = datetime.datetime.fromisoformat(self.target)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ)

    def is_expired(self, at: datetime.datetime | None = None) -> bool:
        return (at or now()) >= self.target_dt

    def fields(self, at: datetime.datetime | None = None) -> dict:
        """算出模板可用的所有變數"""
        at = at or now()
        target = self.target_dt
        # 過期後改用經過的時間，數字才不會是負的
        seconds = abs(int((target - at).total_seconds()))
        return {
            "name": self.name,
            "days": abs((target.date() - at.date()).days),
            "d": seconds // 86400,
            "h": seconds % 86400 // 3600,
            "m": seconds % 3600 // 60,
            "total_days": seconds // 86400,
            "total_hours": seconds // 3600,
            "total_minutes": seconds // 60,
            "target": target.strftime("%Y-%m-%d %H:%M"),
            "target_date": target.strftime("%Y-%m-%d"),
            "target_time": target.strftime("%H:%M"),
        }

    def render(self, at: datetime.datetime | None = None) -> str:
        """算出這個倒數現在應該顯示的頻道名稱"""
        at = at or now()
        template = self.expired_template if self.is_expired(at) else self.template
        return template.format_map(_SafeDict(self.fields(at)))[:NAME_LIMIT]


def validate_template(template: str) -> None:
    """模板寫壞的話直接丟 ValueError，讓指令能當場回報"""
    sample = Countdown(0, 0, "測試", now().isoformat())
    try:
        template.format_map(_SafeDict(sample.fields()))
    except (ValueError, IndexError, KeyError) as e:
        raise ValueError(f"名稱模板格式錯誤：{e}") from e


# ---------------------------------------------------------------- 儲存


def _ensure_dir() -> None:
    directory = os.path.dirname(settings.COUNTDOWN_DATA_FILE)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)


def load_all() -> dict[int, Countdown]:
    """讀出所有倒數，key 為頻道 ID"""
    if not os.path.exists(settings.COUNTDOWN_DATA_FILE):
        return {}
    try:
        with open(settings.COUNTDOWN_DATA_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logging.error(f"倒數資料讀取失敗: {e}")
        return {}

    result = {}
    for channel_id, data in raw.items():
        try:
            result[int(channel_id)] = Countdown(**data)
        except TypeError as e:
            logging.warning(f"略過壞掉的倒數資料 {channel_id}: {e}")
    return result


def save_all(countdowns: dict[int, Countdown]) -> None:
    _ensure_dir()
    raw = {str(cid): asdict(c) for cid, c in countdowns.items()}
    with open(settings.COUNTDOWN_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
