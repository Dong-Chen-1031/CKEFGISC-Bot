"""舊版 JSON 資料的自動遷移

開機時如果資料庫是空的、而舊的 data/*.json 還在，就把資料搬進資料庫。
搬完會在 meta 表留一筆紀錄，之後就不會再搬第二次
（否則使用者手動清空資料後，重開機又會被舊 JSON 灌回來）。
"""

import json
import logging
import os

from sqlmodel import select

import settings

MIGRATED_KEY = "json_migrated_at"


def _read_json(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logging.error(f"舊版 JSON 讀取失敗 ({path}): {e}")
        return None


def _migrate_countdowns(raw: dict) -> int:
    from utils.countdown import Countdown
    from utils.db import session

    moved = 0
    with session() as s:
        for channel_id, data in raw.items():
            try:
                s.add(
                    Countdown(
                        channel_id=int(channel_id),
                        guild_id=int(data["guild_id"]),
                        name=data["name"],
                        target=data["target"],
                        template=data.get("template") or Countdown.model_fields["template"].default,
                        expired_template=data.get("expired_template")
                        or Countdown.model_fields["expired_template"].default,
                    )
                )
                moved += 1
            except (KeyError, ValueError, TypeError) as e:
                logging.warning(f"略過壞掉的倒數資料 {channel_id}: {e}")
        s.commit()
    return moved


def _migrate_broadcasts(raw: dict) -> int:
    from utils.db import session
    from utils.notify import Broadcast, BroadcastTarget

    moved = 0
    with session() as s:
        for bid, data in raw.items():
            try:
                read: dict = data.get("read") or {}
                failed = {int(u) for u in (data.get("failed") or [])}

                bc = Broadcast(
                    id=int(bid),
                    guild_id=int(data["guild_id"]),
                    author_id=int(data["author_id"]),
                    title=data["title"],
                    content=data["content"],
                    created_at=data["created_at"],
                    role_id=data.get("role_id"),
                    channel_id=data.get("channel_id"),
                    anonymous=bool(data.get("anonymous", False)),
                    extra_ids=list(data.get("extra_ids") or []),
                    # 舊資料都是立即寄送，寄出時間用建立時間補上
                    scheduled_at=data.get("scheduled_at"),
                    sent_at=data.get("sent_at") or data["created_at"],
                    remind_every_hours=float(data.get("remind_every_hours", 0)),
                    remind_max=int(data.get("remind_max", 3)),
                    remind_count=int(data.get("remind_count", 0)),
                    last_remind_at=data.get("last_remind_at"),
                )
                s.add(bc)

                for uid in data.get("targets") or []:
                    uid = int(uid)
                    s.add(
                        BroadcastTarget(
                            broadcast_id=bc.id,
                            user_id=uid,
                            read_at=read.get(str(uid)),
                            failed=uid in failed,
                        )
                    )
                moved += 1
            except (KeyError, ValueError, TypeError) as e:
                logging.warning(f"略過壞掉的群發資料 {bid}: {e}")
        s.commit()
    return moved


def migrate_json_if_needed() -> None:
    """資料庫還沒被灌過舊資料的話，就把 data/*.json 搬進來"""
    from utils.db import get_meta, set_meta, session
    from utils.countdown import Countdown
    from utils.notify import Broadcast
    from utils.timeparse import now

    if get_meta(MIGRATED_KEY):
        return  # 已經搬過了

    countdown_raw = _read_json(settings.COUNTDOWN_DATA_FILE)
    notify_raw = _read_json(settings.NOTIFY_DATA_FILE)

    if not countdown_raw and not notify_raw:
        # 沒有舊資料，直接標記完成，之後不用再檢查
        set_meta(MIGRATED_KEY, now().isoformat())
        return

    # 資料庫已經有東西就不要蓋掉，避免重複匯入
    with session() as s:
        has_data = bool(
            s.exec(select(Countdown)).first() or s.exec(select(Broadcast)).first()
        )
    if has_data:
        logging.warning("資料庫已有資料，略過舊版 JSON 遷移")
        set_meta(MIGRATED_KEY, now().isoformat())
        return

    logging.info("偵測到舊版 JSON 資料，開始遷移到資料庫…")
    countdowns = _migrate_countdowns(countdown_raw) if countdown_raw else 0
    broadcasts = _migrate_broadcasts(notify_raw) if notify_raw else 0

    set_meta(MIGRATED_KEY, now().isoformat())
    logging.info(
        f"遷移完成：倒數 {countdowns} 筆、群發 {broadcasts} 筆。"
        f"舊的 JSON 檔已保留，確認無誤後可自行刪除。"
    )
