"""共用的時區與日期解析

倒數和排程通知都要把使用者打的日期字串轉成帶時區的 datetime，集中放這裡。
"""

import datetime
from zoneinfo import ZoneInfo

import settings

TZ = ZoneInfo(settings.TIMEZONE)

_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%m-%d", "%m/%d", "%m.%d")
_TIME_FORMATS = ("%H:%M:%S", "%H:%M", "%H")


def now() -> datetime.datetime:
    return datetime.datetime.now(TZ)


def to_dt(raw: str) -> datetime.datetime:
    """把存進 JSON 的 ISO 字串轉回帶時區的 datetime"""
    dt = datetime.datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


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
        raise ValueError(
            f"看不懂的日期格式：`{date_str}`，請用像 `2025-01-01` 或 `1/1` 的寫法"
        )

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
            raise ValueError(
                f"看不懂的時間格式：`{time_str}`，請用像 `08:00` 的寫法"
            )

    return datetime.datetime.combine(date_part, time_part, tzinfo=TZ)
