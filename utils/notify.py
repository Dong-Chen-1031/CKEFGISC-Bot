"""群發通知的資料模型與儲存

每次群發是一筆 Broadcast，記錄發給了誰、誰按過已讀。
資料存在 `settings.NOTIFY_DATA_FILE`，重啟後已讀按鈕照樣有效。
"""

import datetime
import json
import logging
import os
from dataclasses import dataclass, asdict, field
from zoneinfo import ZoneInfo

import settings

TZ = ZoneInfo(settings.TIMEZONE)


def now() -> datetime.datetime:
    return datetime.datetime.now(TZ)


@dataclass
class Broadcast:
    id: int
    guild_id: int
    author_id: int
    title: str
    content: str
    created_at: str
    # 發給整個身分組時記下 role_id；只發給特定幾個人時為 None
    role_id: int | None = None
    channel_id: int | None = None
    # True 時收件者看不到發布者是誰（/notify status 仍會留紀錄）
    anonymous: bool = False
    targets: list[int] = field(default_factory=list)
    # user_id (str, 因為 JSON 的 key 只能是字串) -> 按下已讀的時間
    read: dict[str, str] = field(default_factory=dict)
    # 私訊送不出去的人 (關閉私訊或封鎖機器人)
    failed: list[int] = field(default_factory=list)

    @property
    def created_dt(self) -> datetime.datetime:
        dt = datetime.datetime.fromisoformat(self.created_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ)

    @property
    def is_role_broadcast(self) -> bool:
        """True 代表發給整個身分組，False 代表只發給指定的幾個人"""
        return self.role_id is not None

    @property
    def delivered(self) -> list[int]:
        """實際收到私訊的人"""
        return [uid for uid in self.targets if uid not in self.failed]

    @property
    def read_ids(self) -> list[int]:
        return [uid for uid in self.targets if str(uid) in self.read]

    @property
    def unread_ids(self) -> list[int]:
        """收到了但還沒按已讀的人 — 送不出去的另外歸在 failed"""
        return [uid for uid in self.delivered if str(uid) not in self.read]

    @property
    def pending_ids(self) -> list[int]:
        """所有還沒確認的人，含私訊失敗的 — 提醒時要一起重試"""
        return [uid for uid in self.targets if str(uid) not in self.read]

    @property
    def progress(self) -> float:
        delivered = self.delivered
        if not delivered:
            return 0.0
        return len(self.read_ids) / len(delivered)

    def has_read(self, user_id: int) -> bool:
        return str(user_id) in self.read

    def mark_read(self, user_id: int) -> bool:
        """按下已讀，回傳 True 代表這次才第一次按"""
        if self.has_read(user_id):
            return False
        self.read[str(user_id)] = now().isoformat()
        return True

    def read_at(self, user_id: int) -> datetime.datetime | None:
        raw = self.read.get(str(user_id))
        if raw is None:
            return None
        dt = datetime.datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ)


# ---------------------------------------------------------------- 儲存


def _ensure_dir() -> None:
    directory = os.path.dirname(settings.NOTIFY_DATA_FILE)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)


def load_all() -> dict[int, Broadcast]:
    """讀出所有群發紀錄，key 為群發編號"""
    if not os.path.exists(settings.NOTIFY_DATA_FILE):
        return {}
    try:
        with open(settings.NOTIFY_DATA_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logging.error(f"群發資料讀取失敗: {e}")
        return {}

    result = {}
    for bid, data in raw.items():
        try:
            result[int(bid)] = Broadcast(**data)
        except TypeError as e:
            logging.warning(f"略過壞掉的群發資料 {bid}: {e}")
    return result


def save_all(broadcasts: dict[int, Broadcast]) -> None:
    _ensure_dir()
    raw = {str(bid): asdict(b) for bid, b in broadcasts.items()}
    with open(settings.NOTIFY_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)


def next_id(broadcasts: dict[int, Broadcast]) -> int:
    return max(broadcasts, default=0) + 1
