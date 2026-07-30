"""日期倒數的資料表與查詢

一個語音頻道同時只會有一個倒數，所以 channel_id 直接當主鍵。
"""

import datetime

from sqlmodel import Field, SQLModel, select

# 時區與日期解析放在共用模組，這裡 re-export 讓 cd.parse_target 等用法照舊
from utils.timeparse import TZ, now, parse_target, to_dt  # noqa: F401

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


class _SafeDict(dict):
    """找不到的變數就原樣保留，避免使用者打錯字直接炸掉"""

    def __missing__(self, key):
        return "{" + key + "}"


class Countdown(SQLModel, table=True):
    __tablename__ = "countdown"

    channel_id: int = Field(primary_key=True)
    guild_id: int = Field(index=True)
    name: str
    target: str  # ISO 8601 字串
    template: str = DEFAULT_TEMPLATE
    expired_template: str = DEFAULT_EXPIRED_TEMPLATE

    @property
    def target_dt(self) -> datetime.datetime:
        return to_dt(self.target)

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
    sample = Countdown(
        channel_id=0, guild_id=0, name="測試", target=now().isoformat()
    )
    try:
        template.format_map(_SafeDict(sample.fields()))
    except (ValueError, IndexError, KeyError) as e:
        raise ValueError(f"名稱模板格式錯誤：{e}") from e


# ---------------------------------------------------------------- 查詢


def all_countdowns() -> list[Countdown]:
    from utils.db import session

    with session() as s:
        return list(s.exec(select(Countdown)).all())


def guild_countdowns(guild_id: int) -> list[Countdown]:
    from utils.db import session

    with session() as s:
        return list(
            s.exec(select(Countdown).where(Countdown.guild_id == guild_id)).all()
        )


def get_countdown(channel_id: int) -> Countdown | None:
    from utils.db import session

    with session() as s:
        return s.get(Countdown, channel_id)


def save_countdown(entry: Countdown) -> Countdown:
    """新增或更新（channel_id 是主鍵，merge 會自動判斷）"""
    from utils.db import session

    with session() as s:
        merged = s.merge(entry)
        s.commit()
        return merged


def delete_countdown(channel_id: int) -> bool:
    """回傳 True 代表本來有這筆、已經刪掉"""
    from utils.db import session

    with session() as s:
        row = s.get(Countdown, channel_id)
        if row is None:
            return False
        s.delete(row)
        s.commit()
        return True
