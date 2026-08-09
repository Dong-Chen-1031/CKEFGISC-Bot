"""訊息紀錄的資料表與查詢

一個伺服器只會有一組設定，所以 guild_id 直接當主鍵。
要不要真的記錄還得看 settings.MESSAGE_LOG_ENABLED 這個總開關 ——
總開關關掉之後所有伺服器一律停止記錄，但資料庫裡的設定會原封不動留著，
之後再打開就直接恢復原本的設定。
"""

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel, select

import settings
from utils.timeparse import now

# /msglog config 可以調整的開關 (欄位名 -> (顯示名稱, 說明))
# 指令選項、狀態嵌入都是從這張表長出來的，加欄位時只要改這裡
TOGGLES: dict[str, tuple[str, str]] = {
    "log_delete": ("刪除通知", "訊息被刪除時發送通知"),
    "log_edit": ("編輯通知", "訊息被編輯時發送通知"),
    "log_bulk_delete": ("批次刪除通知", "訊息被整批清除時發送彙整通知"),
    "ignore_bots": ("忽略機器人", "不記錄機器人與 Webhook 的訊息"),
    "include_content": ("記錄內容", "關掉之後只留下作者、頻道等資訊，不留訊息文字"),
    "log_attachments": ("記錄附件", "列出附件、貼圖、投票等非文字內容"),
    "log_embed_only_edits": (
        "記錄連結預覽",
        "Discord 幫訊息補上連結預覽時也算一次編輯，預設不記錄",
    ),
    "check_audit_log": ("查詢稽核紀錄", "嘗試從稽核紀錄找出是誰刪的（需要「查看稽核日誌」權限）"),
}


class MessageLogConfig(SQLModel, table=True):
    """單一伺服器的訊息紀錄設定"""

    __tablename__ = "message_log_config"

    guild_id: int = Field(primary_key=True)
    # 通知要送到哪個頻道
    channel_id: int
    # 這個伺服器自己的開關，跟 settings.MESSAGE_LOG_ENABLED 是「而且」的關係
    enabled: bool = True

    # ── 各項開關，每一項的說明見上面的 TOGGLES ──
    log_delete: bool = True
    log_edit: bool = True
    log_bulk_delete: bool = True
    ignore_bots: bool = True
    include_content: bool = True
    log_attachments: bool = True
    log_embed_only_edits: bool = False
    check_audit_log: bool = True

    # ── 例外清單 ──
    ignored_channels: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    ignored_users: list[int] = Field(default_factory=list, sa_column=Column(JSON))

    updated_at: str = Field(default_factory=lambda: now().isoformat())
    updated_by: int | None = None

    # ---------------------------------------------------------- 判斷

    @property
    def active(self) -> bool:
        """總開關與伺服器開關都打開才算真的在記錄"""
        return settings.MESSAGE_LOG_ENABLED and self.enabled

    def ignores(
        self,
        *,
        channel_id: int,
        parent_id: int | None = None,
        author_id: int | None = None,
        author_is_bot: bool = False,
    ) -> str | None:
        """要略過的話回傳原因，要記錄的話回傳 None

        原因只寫進 log 方便排查，不會出現在通知裡。
        """
        if channel_id == self.channel_id:
            return "紀錄頻道本身"
        if channel_id in self.ignored_channels:
            return "頻道在忽略清單"
        # 討論串沿用父頻道的設定，否則忽略一個頻道還是會被串裡的訊息洗版
        if parent_id is not None and parent_id in self.ignored_channels:
            return "父頻道在忽略清單"
        if author_id is not None and author_id in self.ignored_users:
            return "作者在忽略清單"
        if author_is_bot and self.ignore_bots:
            return "作者是機器人"
        return None

    def toggle_summary(self) -> list[tuple[str, bool]]:
        """(顯示名稱, 目前值) 的清單，給狀態嵌入用"""
        return [(label, getattr(self, key)) for key, (label, _) in TOGGLES.items()]


# ---------------------------------------------------------------- 查詢


def get_config(guild_id: int) -> MessageLogConfig | None:
    from utils.db import session

    with session() as s:
        return s.get(MessageLogConfig, guild_id)


def all_configs() -> list[MessageLogConfig]:
    from utils.db import session

    with session() as s:
        return list(s.exec(select(MessageLogConfig)).all())


def save_config(cfg: MessageLogConfig, updated_by: int | None = None) -> MessageLogConfig:
    """新增或更新（guild_id 是主鍵，merge 會自動判斷）"""
    from utils.db import session

    cfg.updated_at = now().isoformat()
    if updated_by is not None:
        cfg.updated_by = updated_by

    with session() as s:
        merged = s.merge(cfg)
        s.commit()
        return merged


def delete_config(guild_id: int) -> bool:
    """回傳 True 代表本來有這筆、已經刪掉"""
    from utils.db import session

    with session() as s:
        row = s.get(MessageLogConfig, guild_id)
        if row is None:
            return False
        s.delete(row)
        s.commit()
        return True


def set_ignored(
    guild_id: int,
    *,
    channels: list[int] | None = None,
    users: list[int] | None = None,
) -> MessageLogConfig | None:
    """整批換掉忽略清單

    JSON 欄位要指派新的 list 才會被判定成有更動，就地 append 不會寫進資料庫，
    所以這裡統一由呼叫端算好完整清單再丟進來。
    """
    from utils.db import session

    with session() as s:
        row = s.get(MessageLogConfig, guild_id)
        if row is None:
            return None
        if channels is not None:
            row.ignored_channels = list(dict.fromkeys(channels))
        if users is not None:
            row.ignored_users = list(dict.fromkeys(users))
        row.updated_at = now().isoformat()
        s.add(row)
        s.commit()
        return row
