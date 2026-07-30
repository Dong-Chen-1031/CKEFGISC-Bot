"""群發通知的資料表與查詢

每次群發是一筆 Broadcast，收件者拆成 BroadcastTarget 一人一列，
所以「誰已讀、誰沒讀、誰收不到」都是欄位而不是塞在 JSON 裡。
"""

import datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, Relationship, SQLModel, select

from utils.timeparse import TZ, now, to_dt  # noqa: F401


class BroadcastTarget(SQLModel, table=True):
    """一則通知的其中一位收件者"""

    __tablename__ = "broadcast_target"

    broadcast_id: int = Field(
        foreign_key="broadcast.id", primary_key=True, ondelete="CASCADE"
    )
    user_id: int = Field(primary_key=True)
    # 按下已讀的時間，None 代表還沒按
    read_at: str | None = None
    # 私訊送不出去（關閉私訊或封鎖機器人）
    failed: bool = False

    broadcast: "Broadcast" = Relationship(back_populates="recipients")


class Broadcast(SQLModel, table=True):
    __tablename__ = "broadcast"

    id: int | None = Field(default=None, primary_key=True)
    guild_id: int = Field(index=True)
    author_id: int
    title: str
    content: str
    created_at: str
    # 發給整個身分組時記下 role_id；只發給特定幾個人時為 None
    role_id: int | None = None
    channel_id: int | None = None
    # True 時收件者看不到發布者是誰（/notify status 仍會留紀錄）
    anonymous: bool = False
    # 單獨指定的成員，排程時要等到寄送當下才解析成收件者
    extra_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))

    # ── 排程 ──
    scheduled_at: str | None = None  # 預定寄送時間，None 代表立即寄送
    sent_at: str | None = None  # 實際寄出時間，排程中為 None

    # ── 自動提醒未讀 ──
    remind_every_hours: float = 0  # 0 代表關閉
    remind_max: int = 3
    remind_count: int = 0
    last_remind_at: str | None = None

    recipients: list[BroadcastTarget] = Relationship(
        back_populates="broadcast",
        cascade_delete=True,
        sa_relationship_kwargs={"lazy": "selectin"},
    )

    # ---------------------------------------------------------- 基本

    @property
    def created_dt(self) -> datetime.datetime:
        return to_dt(self.created_at)

    @property
    def is_role_broadcast(self) -> bool:
        """True 代表發給整個身分組，False 代表只發給指定的幾個人"""
        return self.role_id is not None

    # ---------------------------------------------------------- 排程

    @property
    def scheduled_dt(self) -> datetime.datetime | None:
        return to_dt(self.scheduled_at) if self.scheduled_at else None

    @property
    def is_pending(self) -> bool:
        """排程中、還沒寄出

        只有排程通知會處於這個狀態；立即寄送的通知沒有 scheduled_at，
        所以早期沒有 sent_at 的舊資料不會被誤判成待寄送。
        """
        return self.scheduled_at is not None and self.sent_at is None

    def is_due(self, at: datetime.datetime | None = None) -> bool:
        if not self.is_pending:
            return False
        return (at or now()) >= self.scheduled_dt

    @property
    def sent_dt(self) -> datetime.datetime:
        """實際寄出時間，舊資料沒有就退回建立時間"""
        return to_dt(self.sent_at or self.created_at)

    # ---------------------------------------------------------- 收件者

    @property
    def targets(self) -> list[int]:
        return [r.user_id for r in self.recipients]

    @property
    def failed(self) -> list[int]:
        return [r.user_id for r in self.recipients if r.failed]

    @property
    def delivered(self) -> list[int]:
        """實際收到私訊的人"""
        return [r.user_id for r in self.recipients if not r.failed]

    @property
    def read_ids(self) -> list[int]:
        return [r.user_id for r in self.recipients if r.read_at]

    @property
    def unread_ids(self) -> list[int]:
        """收到了但還沒按已讀的人 — 送不出去的另外歸在 failed"""
        return [r.user_id for r in self.recipients if not r.failed and not r.read_at]

    @property
    def pending_ids(self) -> list[int]:
        """所有還沒確認的人，含私訊失敗的 — 提醒時要一起重試"""
        return [r.user_id for r in self.recipients if not r.read_at]

    @property
    def progress(self) -> float:
        delivered = self.delivered
        if not delivered:
            return 0.0
        return len(self.read_ids) / len(delivered)

    def recipient(self, user_id: int) -> BroadcastTarget | None:
        return next((r for r in self.recipients if r.user_id == user_id), None)

    def has_read(self, user_id: int) -> bool:
        r = self.recipient(user_id)
        return bool(r and r.read_at)

    def read_at(self, user_id: int) -> datetime.datetime | None:
        r = self.recipient(user_id)
        return to_dt(r.read_at) if r and r.read_at else None

    # ---------------------------------------------------------- 自動提醒

    @property
    def auto_remind_on(self) -> bool:
        return self.remind_every_hours > 0

    @property
    def next_remind_dt(self) -> datetime.datetime | None:
        """下一次自動提醒的時間，沒開或已用完次數則為 None"""
        if not self.auto_remind_on or self.is_pending:
            return None
        if self.remind_count >= self.remind_max:
            return None
        base = to_dt(self.last_remind_at) if self.last_remind_at else self.sent_dt
        return base + datetime.timedelta(hours=self.remind_every_hours)

    def needs_remind(self, at: datetime.datetime | None = None) -> bool:
        """該不該現在自動提醒 — 還有沒確認的人、次數沒用完、且時間到了"""
        nxt = self.next_remind_dt
        if nxt is None or not self.pending_ids:
            return False
        return (at or now()) >= nxt


# ---------------------------------------------------------------- 查詢


def all_broadcasts() -> list[Broadcast]:
    from utils.db import session

    with session() as s:
        return list(s.exec(select(Broadcast)).all())


def guild_broadcasts(guild_id: int) -> list[Broadcast]:
    from utils.db import session

    with session() as s:
        return list(
            s.exec(select(Broadcast).where(Broadcast.guild_id == guild_id)).all()
        )


def get_broadcast(bid: int) -> Broadcast | None:
    from utils.db import session

    with session() as s:
        return s.get(Broadcast, bid)


def create_broadcast(bc: Broadcast) -> Broadcast:
    """寫入一筆新的群發，回傳帶有自動編號的物件"""
    from utils.db import session

    with session() as s:
        s.add(bc)
        s.commit()
        s.refresh(bc)
        return bc


def delete_broadcast(bid: int) -> bool:
    """回傳 True 代表本來有這筆、已經刪掉（收件者會一起級聯刪除）"""
    from utils.db import session

    with session() as s:
        row = s.get(Broadcast, bid)
        if row is None:
            return False
        s.delete(row)
        s.commit()
        return True


def set_recipients(bid: int, user_ids: list[int]) -> None:
    """把某則通知的收件者整批換成這一組（寄送當下才決定）"""
    from utils.db import session

    with session() as s:
        for r in s.exec(
            select(BroadcastTarget).where(BroadcastTarget.broadcast_id == bid)
        ).all():
            s.delete(r)
        for uid in user_ids:
            s.add(BroadcastTarget(broadcast_id=bid, user_id=uid))
        s.commit()


def set_failed(bid: int, user_id: int, failed: bool) -> None:
    """更新單一收件者的私訊成功與否"""
    from utils.db import session

    with session() as s:
        r = s.get(BroadcastTarget, (bid, user_id))
        if r is not None and r.failed != failed:
            r.failed = failed
            s.add(r)
            s.commit()


def mark_read(bid: int, user_id: int) -> bool:
    """按下已讀，回傳 True 代表這次才第一次按"""
    from utils.db import session

    with session() as s:
        r = s.get(BroadcastTarget, (bid, user_id))
        if r is None or r.read_at:
            return False
        r.read_at = now().isoformat()
        s.add(r)
        s.commit()
        return True


def update_broadcast(bc: Broadcast) -> Broadcast:
    """把改過的欄位寫回資料庫

    只更新純欄位，不碰 recipients。這裡不能用 session.merge()：
    傳進來的物件常常帶著一份過時的 recipients（例如剛建立時是空的），
    merge 會把那份空集合一起套用，級聯刪掉剛寫進去的收件者。
    """
    from utils.db import session

    with session() as s:
        row = s.get(Broadcast, bc.id)
        if row is None:
            return bc
        for key, value in bc.model_dump(exclude={"id"}).items():
            setattr(row, key, value)
        s.add(row)
        s.commit()
        return s.get(Broadcast, bc.id)
