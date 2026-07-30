"""資料庫連線與初始化

用 SQLModel (SQLAlchemy + SQLite)。所有資料表定義在 utils/countdown.py 和
utils/notify.py，這裡只負責 engine、session 與開機時的建表 / 自動遷移。
"""

import logging
import os

from sqlalchemy import event
from sqlmodel import Field, Session, SQLModel, create_engine, select

import settings


class Meta(SQLModel, table=True):
    """雜項設定，目前只用來記錄 JSON 是否已經遷移過"""

    __tablename__ = "meta"

    key: str = Field(primary_key=True)
    value: str


def _ensure_dir() -> None:
    directory = os.path.dirname(settings.DATABASE_FILE)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)


_ensure_dir()

engine = create_engine(
    f"sqlite:///{settings.DATABASE_FILE}",
    echo=False,
    # bot 是單一行程，但 discord.py 的執行緒池偶爾會碰到連線
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    # WAL 讓讀寫不互相阻塞；foreign_keys 預設是關的，要打開才有級聯刪除
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


def session() -> Session:
    """開一個 session

    expire_on_commit=False：commit 之後物件仍可讀取欄位，
    否則離開 with 區塊後再碰屬性會觸發重新查詢而炸掉。
    """
    return Session(engine, expire_on_commit=False)


def get_meta(key: str) -> str | None:
    with session() as s:
        row = s.get(Meta, key)
        return row.value if row else None


def set_meta(key: str, value: str) -> None:
    with session() as s:
        row = s.get(Meta, key)
        if row:
            row.value = value
        else:
            row = Meta(key=key, value=value)
        s.add(row)
        s.commit()


def init_db() -> None:
    """建表，接著在需要時把舊的 JSON 資料搬進來"""
    # import 這兩個模組才會把資料表註冊到 SQLModel.metadata
    from utils import countdown, notify  # noqa: F401

    SQLModel.metadata.create_all(engine)
    logging.info(f"資料庫已就緒: {settings.DATABASE_FILE}")

    from utils.migrate import migrate_json_if_needed

    migrate_json_if_needed()
