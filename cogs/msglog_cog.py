"""訊息紀錄

訊息被刪除或編輯時，往指定頻道送一則整理好的通知。
設定用 /msglog 指令調整，只有 settings.DEV_ID 裡的開發者能使用，
而且 settings.MESSAGE_LOG_ENABLED 這個總開關要是開著的。

全部用 raw 事件 (on_raw_message_*) 監聽：一般的 on_message_delete 只有在訊息
還在快取裡才會觸發，重開機前的舊訊息被刪掉就完全不會有通知。raw 事件一定會來，
拿不到快取時就退而求其次，能記多少記多少。
"""

import datetime
import logging

import discord
from discord import Interaction, app_commands
from discord.ext import commands

import settings
from utils import msglog as ml
from utils import ui
from utils.log import log
from utils.timeparse import to_dt

# Discord 單一 embed 欄位的字數上限
FIELD_LIMIT = 1024
# 單一 embed 的欄位數與總字數上限
MAX_FIELDS = 25
EMBED_LIMIT = 6000
# 內容太長時最多拆成幾個欄位
MAX_CONTENT_FIELDS = 3
# 稽核紀錄要多新才算得上是這次刪除的
AUDIT_MAX_AGE = datetime.timedelta(seconds=15)

COLOR_DELETE = discord.Color.from_rgb(237, 66, 69)
COLOR_EDIT = discord.Color.from_rgb(250, 166, 26)
COLOR_BULK = discord.Color.from_rgb(153, 45, 34)

# 非一般訊息的類型說明，沒列到的就直接顯示原本的英文名稱
MESSAGE_TYPE_LABELS = {
    discord.MessageType.recipient_add: "加入成員",
    discord.MessageType.recipient_remove: "移除成員",
    discord.MessageType.call: "通話",
    discord.MessageType.channel_name_change: "頻道改名",
    discord.MessageType.channel_icon_change: "頻道圖示變更",
    discord.MessageType.pins_add: "釘選訊息",
    discord.MessageType.new_member: "新成員加入",
    discord.MessageType.premium_guild_subscription: "伺服器加成",
    discord.MessageType.channel_follow_add: "追蹤頻道",
    discord.MessageType.thread_created: "建立討論串",
    discord.MessageType.thread_starter_message: "討論串起始訊息",
    discord.MessageType.guild_invite_reminder: "邀請提醒",
    discord.MessageType.chat_input_command: "斜線指令回應",
    discord.MessageType.context_menu_command: "右鍵選單指令回應",
    discord.MessageType.auto_moderation_action: "自動審核",
    discord.MessageType.role_subscription_purchase: "身分組訂閱",
    discord.MessageType.stage_start: "舞台開始",
    discord.MessageType.stage_end: "舞台結束",
    discord.MessageType.poll_result: "投票結果",
}


class NotDeveloper(app_commands.CheckFailure):
    """不在 settings.DEV_ID 裡"""


class FeatureDisabled(app_commands.CheckFailure):
    """settings.MESSAGE_LOG_ENABLED 是關的"""


def dev_only():
    """只有設定檔裡的開發者能用，而且總開關要開著"""

    async def predicate(interaction: Interaction) -> bool:
        if interaction.user.id not in settings.DEV_ID:
            raise NotDeveloper()
        if not settings.MESSAGE_LOG_ENABLED:
            raise FeatureDisabled()
        return True

    return app_commands.check(predicate)


# ---------------------------------------------------------------- 小工具


def human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def trim(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def add_text_field(embed: discord.Embed, name: str, text: str) -> None:
    """加一個文字欄位，太長就截斷、必要時拆成好幾欄

    Discord 單一欄位只吃 1024 字，但訊息可以到 4000 字（Nitro），
    所以先照設定檔的上限截斷，再依欄位上限切塊。
    """
    text = trim(text, settings.MESSAGE_LOG_CONTENT_LIMIT)
    chunks = [text[i : i + FIELD_LIMIT] for i in range(0, len(text), FIELD_LIMIT)]
    chunks = chunks[:MAX_CONTENT_FIELDS] or [""]
    for i, chunk in enumerate(chunks):
        title = name if i == 0 else f"{name}（續 {i + 1}）"
        embed.add_field(name=title, value=chunk or "*（空白）*", inline=False)


def clamp_embed(embed: discord.Embed) -> discord.Embed:
    """把 embed 修剪到 Discord 的上限內

    一則訊息同時塞滿長文字、附件、貼圖、投票時，欄位數或總字數有機會爆掉，
    那樣整則通知會被 Discord 退回。寧可從尾巴砍掉幾欄，也不要整則不見。
    """
    trimmed = False
    while embed.fields and (len(embed.fields) > MAX_FIELDS or len(embed) > EMBED_LIMIT):
        embed.remove_field(-1)
        trimmed = True

    if trimmed:
        # 砍完還得再空出放提示的位置
        note = "內容過長，部分欄位已省略"
        while embed.fields and (
            len(embed.fields) >= MAX_FIELDS or len(embed) + len(note) + 2 > EMBED_LIMIT
        ):
            embed.remove_field(-1)
        embed.add_field(name="⚠️", value=note, inline=False)
    return embed


def attachment_icon(att: discord.Attachment) -> str:
    if att.is_voice_message():
        return "🎙️"
    kind = (att.content_type or "").split("/")[0]
    return {"image": "🖼️", "video": "🎬", "audio": "🎵", "text": "📄"}.get(kind, "📎")


def describe_attachments(attachments) -> str:
    """把附件列成一行一個，附上大小與原始連結"""
    lines = []
    for att in attachments:
        name = att.filename
        if att.is_spoiler():
            name = f"{name}（劇透）"
        note = "語音訊息" if att.is_voice_message() else human_size(att.size)
        lines.append(f"{attachment_icon(att)} [{name}]({att.url}) — {note}")
    return "\n".join(lines)


def describe_embeds(embeds: list[discord.Embed]) -> str:
    lines = []
    for e in embeds:
        title = e.title or (e.author.name if e.author else None) or e.url or "無標題"
        lines.append(f"・`{e.type}` {trim(title, 80)}")
    return "\n".join(lines)


def describe_poll(poll: discord.Poll) -> str:
    """投票的題目與各選項得票數"""
    question = getattr(poll.question, "text", None) or str(poll.question)
    lines = [f"**{trim(question, 200)}**"]
    for answer in poll.answers:
        emoji = f"{answer.emoji} " if answer.emoji else ""
        lines.append(f"・{emoji}{trim(answer.text or '（無文字）', 80)} — {answer.vote_count} 票")
    if poll.multiple:
        lines.append("*（可複選）*")
    return "\n".join(lines)


def channel_line(channel: discord.abc.GuildChannel | discord.Thread | None, channel_id: int) -> str:
    """頻道的顯示文字，討論串會一併標出父頻道"""
    if channel is None:
        return f"<#{channel_id}>"
    if isinstance(channel, discord.Thread):
        parent = f"<#{channel.parent_id}>" if channel.parent_id else "未知頻道"
        return f"🧵 {channel.mention}（位於 {parent}）"
    return channel.mention


class MessageLogCog(commands.Cog):
    """訊息刪除 / 編輯的通知，以及 /msglog 設定指令"""

    msglog = app_commands.Group(
        name="msglog",
        description="訊息紀錄設定（開發者專用）",
        # 真正的權限檢查是 dev_only()，這裡只是順便讓一般成員看不到指令
        default_permissions=discord.Permissions(administrator=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------------------------------------------ 送出通知

    async def send_log(
        self,
        cfg: ml.MessageLogConfig,
        embed: discord.Embed,
        view: discord.ui.View | None = None,
    ) -> bool:
        """把通知送到設定好的頻道，送不出去只記在 log 不吵使用者"""
        channel = self.bot.get_channel(cfg.channel_id)
        if channel is None:
            logging.warning(
                f"訊息紀錄：找不到伺服器 {cfg.guild_id} 的紀錄頻道 {cfg.channel_id}"
            )
            return False

        try:
            await channel.send(embed=clamp_embed(embed), view=view or discord.utils.MISSING)
        except discord.Forbidden:
            logging.warning(
                f"訊息紀錄：沒有權限在 {cfg.channel_id} 發送訊息（需要「發送訊息」與「嵌入連結」）"
            )
            return False
        except discord.HTTPException as e:
            logging.warning(f"訊息紀錄：通知發送失敗 ({cfg.channel_id}): {e}")
            return False
        return True

    def config_for(self, guild_id: int | None) -> ml.MessageLogConfig | None:
        """拿這個伺服器啟用中的設定，沒開就回 None"""
        if guild_id is None:  # 私訊不記錄
            return None
        if not settings.MESSAGE_LOG_ENABLED:
            return None
        cfg = ml.get_config(guild_id)
        if cfg is None or not cfg.enabled:
            return None
        return cfg

    async def find_deleter(
        self, guild: discord.Guild, author_id: int | None, channel_id: int
    ) -> discord.User | discord.Member | None:
        """從稽核紀錄猜是誰刪的

        只是「猜」：自己刪自己的訊息不會留稽核紀錄，而且 Discord 會把短時間內的
        多次刪除合併成同一筆，所以找不到就當作是本人刪的，不寫進通知裡。
        """
        me = guild.me
        if me is None or not me.guild_permissions.view_audit_log:
            return None

        cutoff = discord.utils.utcnow() - AUDIT_MAX_AGE
        try:
            async for entry in guild.audit_logs(
                limit=5, action=discord.AuditLogAction.message_delete
            ):
                if entry.created_at < cutoff:
                    break
                if author_id is not None and (entry.target is None or entry.target.id != author_id):
                    continue
                extra_channel = getattr(entry.extra, "channel", None)
                if extra_channel is not None and extra_channel.id != channel_id:
                    continue
                return entry.user
        except (discord.Forbidden, discord.HTTPException) as e:
            logging.debug(f"訊息紀錄：讀取稽核紀錄失敗: {e}")
        return None

    # ------------------------------------------------------------ 組嵌入

    def base_embed(
        self,
        *,
        title: str,
        color: discord.Color,
        author: discord.User | discord.Member | None,
        channel,
        channel_id: int,
        message_id: int,
    ) -> discord.Embed:
        """所有通知共用的骨架：誰、在哪個頻道、什麼時候"""
        embed = discord.Embed(title=title, color=color, timestamp=discord.utils.utcnow())

        if author is not None:
            embed.set_author(name=str(author), icon_url=author.display_avatar.url)
            who = f"{author.mention}（`{author.id}`）"
            if author.bot:
                who += "　🤖 機器人"
        else:
            who = "未知（訊息不在快取中）"

        embed.add_field(name="作者", value=who, inline=True)
        embed.add_field(name="頻道", value=channel_line(channel, channel_id), inline=True)
        embed.set_footer(text=f"訊息 ID：{message_id}")
        return embed

    def add_message_body(
        self,
        embed: discord.Embed,
        cfg: ml.MessageLogConfig,
        message: discord.Message,
        *,
        include_content: bool = True,
    ) -> None:
        """把一則訊息的各種內容攤平成欄位

        文字只是其中一種：附件、貼圖、投票、轉發、系統訊息都各自有欄位，
        真的什麼都沒有（例如只有元件的機器人訊息）就標成「沒有文字內容」。
        編輯通知的內容是分成前後兩段自己組的，所以那邊會傳 include_content=False。
        """
        if include_content:
            if not cfg.include_content:
                add_text_field(embed, "內容", "*（這個伺服器關閉了內容記錄）*")
            else:
                add_text_field(embed, "內容", message.content or "*（沒有文字內容）*")

        # 回覆
        ref = message.reference
        if ref is not None and message.type is discord.MessageType.reply:
            embed.add_field(
                name="回覆",
                value=f"[跳到原訊息]({ref.jump_url})",
                inline=True,
            )

        if not cfg.log_attachments:
            return

        # 轉發（Discord 的「轉發訊息」會把原文放在 snapshot 裡）
        snapshots = getattr(message, "message_snapshots", None) or []
        if snapshots:
            previews = []
            for snap in snapshots:
                text = snap.content or "（沒有文字）"
                extra = []
                if snap.attachments:
                    extra.append(f"{len(snap.attachments)} 個附件")
                if snap.embeds:
                    extra.append(f"{len(snap.embeds)} 個嵌入")
                suffix = f"（{'、'.join(extra)}）" if extra else ""
                previews.append(f"{trim(text, 300)}{suffix}")
            add_text_field(embed, "🔁 轉發內容", "\n".join(previews))

        if message.attachments:
            add_text_field(
                embed,
                f"附件（{len(message.attachments)}）",
                describe_attachments(message.attachments),
            )
            # 圖片附件多附一張預覽。訊息被刪掉之後這個連結就失效了，
            # 但通知是即時送出的，多數情況下當下還看得到。
            first = message.attachments[0]
            if (first.content_type or "").startswith("image") and not first.is_spoiler():
                embed.set_image(url=first.url)

        if message.stickers:
            add_text_field(
                embed,
                f"貼圖（{len(message.stickers)}）",
                "\n".join(f"・[{s.name}]({s.url})" for s in message.stickers),
            )

        if message.poll is not None:
            add_text_field(embed, "📊 投票", describe_poll(message.poll))

        # 使用者自己貼的連結預覽沒什麼記錄價值，機器人的 embed 才有
        if message.embeds and (message.author.bot or message.webhook_id):
            add_text_field(embed, f"嵌入（{len(message.embeds)}）", describe_embeds(message.embeds))

        # components 不一定會出現在事件裡，沒有的話 discord.py 根本不會設這個屬性
        components = getattr(message, "components", None)
        if components:
            embed.add_field(name="元件", value=f"{len(components)} 列按鈕／選單", inline=True)

        if message.webhook_id:
            embed.add_field(name="來源", value="Webhook 訊息", inline=True)

        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            label = MESSAGE_TYPE_LABELS.get(message.type, message.type.name)
            embed.add_field(name="訊息類型", value=label, inline=True)

    def jump_view(self, url: str, label: str = "跳到訊息") -> discord.ui.View:
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label=label, url=url, style=discord.ButtonStyle.link))
        return view

    # ------------------------------------------------------------ 事件

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        cfg = self.config_for(payload.guild_id)
        if cfg is None or not cfg.log_delete:
            return

        message = payload.cached_message
        channel = self.bot.get_channel(payload.channel_id)
        parent_id = channel.parent_id if isinstance(channel, discord.Thread) else None
        author = message.author if message else None

        reason = cfg.ignores(
            channel_id=payload.channel_id,
            parent_id=parent_id,
            author_id=author.id if author else None,
            author_is_bot=bool(author and (author.bot or (message and message.webhook_id))),
        )
        if reason:
            return

        embed = self.base_embed(
            title="🗑️ 訊息被刪除",
            color=COLOR_DELETE,
            author=author,
            channel=channel,
            channel_id=payload.channel_id,
            message_id=payload.message_id,
        )

        if message is None:
            # 快取裡沒有 = 機器人啟動前就存在的舊訊息，只剩下 ID 跟頻道
            embed.add_field(
                name="內容",
                value="*（訊息不在快取中，無法取得內容。通常是機器人啟動前就存在的舊訊息）*",
                inline=False,
            )
        else:
            embed.add_field(
                name="發送時間",
                value=f"<t:{int(message.created_at.timestamp())}:f>",
                inline=True,
            )
            self.add_message_body(embed, cfg, message)

        if cfg.check_audit_log:
            guild = self.bot.get_guild(payload.guild_id)
            if guild is not None:
                deleter = await self.find_deleter(
                    guild, author.id if author else None, payload.channel_id
                )
                if deleter is not None:
                    embed.add_field(
                        name="刪除者",
                        value=f"{deleter.mention}（`{deleter.id}`）\n*來自稽核紀錄，僅供參考*",
                        inline=False,
                    )

        await self.send_log(cfg, embed)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        cfg = self.config_for(payload.guild_id)
        if cfg is None or not cfg.log_edit:
            return

        after = payload.message
        before = payload.cached_message

        # Discord 幫訊息補上連結預覽時也會送 MESSAGE_UPDATE，但那不是使用者編輯的，
        # edited_at 會是空的。內容沒變的更新（釘選、抑制嵌入等）也一併跳過。
        embed_only = after.edited_at is None or (
            before is not None and before.content == after.content
        )
        if embed_only and not cfg.log_embed_only_edits:
            return

        channel = self.bot.get_channel(payload.channel_id) or after.channel
        parent_id = channel.parent_id if isinstance(channel, discord.Thread) else None

        reason = cfg.ignores(
            channel_id=payload.channel_id,
            parent_id=parent_id,
            author_id=after.author.id if after.author else None,
            author_is_bot=bool(after.author and after.author.bot) or bool(after.webhook_id),
        )
        if reason:
            return

        embed = self.base_embed(
            title="✏️ 訊息被編輯",
            color=COLOR_EDIT,
            author=after.author,
            channel=channel,
            channel_id=payload.channel_id,
            message_id=payload.message_id,
        )

        if not cfg.include_content:
            embed.add_field(name="內容", value="*（這個伺服器關閉了內容記錄）*", inline=False)
        else:
            if before is None:
                # 快取裡沒有舊訊息，只好單獨呈現編輯後的內容
                add_text_field(embed, "修改前", "*（訊息不在快取中，無法取得原本的內容）*")
            else:
                add_text_field(embed, "修改前", before.content or "*（沒有文字內容）*")
            add_text_field(embed, "修改後", after.content or "*（沒有文字內容）*")

        # 內容前面已經處理完，這裡只補附件、貼圖、投票等其他部分
        self.add_message_body(embed, cfg, after, include_content=False)

        if embed_only:
            embed.add_field(name="備註", value="這次更新只有連結預覽變動", inline=False)

        # 編輯過的訊息還在，附上跳轉按鈕
        await self.send_log(cfg, embed, view=self.jump_view(after.jump_url))

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent):
        cfg = self.config_for(payload.guild_id)
        if cfg is None or not cfg.log_bulk_delete:
            return

        channel = self.bot.get_channel(payload.channel_id)
        parent_id = channel.parent_id if isinstance(channel, discord.Thread) else None
        if cfg.ignores(channel_id=payload.channel_id, parent_id=parent_id):
            return

        cached = sorted(payload.cached_messages, key=lambda m: m.created_at)
        embed = discord.Embed(
            title="🧹 訊息被批次刪除",
            color=COLOR_BULK,
            timestamp=discord.utils.utcnow(),
            description=(
                f"在 {channel_line(channel, payload.channel_id)} 一次刪除了 "
                f"**{len(payload.message_ids)}** 則訊息"
            ),
        )

        if cached and cfg.include_content:
            lines = []
            for message in cached[: settings.MESSAGE_LOG_BULK_PREVIEW]:
                extras = []
                if message.attachments:
                    extras.append(f"📎×{len(message.attachments)}")
                if message.stickers:
                    extras.append(f"🖼️×{len(message.stickers)}")
                suffix = f"　{' '.join(extras)}" if extras else ""
                body = trim(message.content or "（沒有文字內容）", 120)
                lines.append(f"**{message.author.display_name}**：{body}{suffix}")
            add_text_field(embed, f"內容預覽（{len(lines)}／{len(payload.message_ids)}）", "\n".join(lines))

        missing = len(payload.message_ids) - len(cached)
        if missing > 0:
            embed.add_field(
                name="備註", value=f"其中 {missing} 則不在快取中，無法取得內容", inline=False
            )

        embed.set_footer(text=f"頻道 ID：{payload.channel_id}")
        await self.send_log(cfg, embed)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        """紀錄頻道被刪掉就自動停用，免得之後每則訊息都在 log 裡噴錯"""
        cfg = ml.get_config(channel.guild.id)
        if cfg is None:
            return

        if cfg.channel_id == channel.id and cfg.enabled:
            cfg.enabled = False
            ml.save_config(cfg)
            logging.warning(
                f"訊息紀錄：伺服器 {channel.guild.id} 的紀錄頻道已被刪除，已自動停用"
            )
        elif channel.id in cfg.ignored_channels:
            ml.set_ignored(
                cfg.guild_id, channels=[c for c in cfg.ignored_channels if c != channel.id]
            )

    # ------------------------------------------------------------ 指令用小工具

    def resolve_guild(self, interaction: Interaction, guild_id: str | None) -> discord.Guild:
        """指令裡的 guild 參數 -> Guild 物件，沒給就用目前所在的伺服器"""
        if guild_id:
            raw = guild_id.strip()
            if not raw.isdigit():
                raise ValueError(f"`{raw}` 不是有效的伺服器 ID")
            guild = self.bot.get_guild(int(raw))
            if guild is None:
                raise ValueError(f"我不在 ID 為 `{raw}` 的伺服器裡")
            return guild
        if interaction.guild is not None:
            return interaction.guild
        raise ValueError("在私訊裡使用時必須指定 `guild` 參數")

    def require_config(self, guild: discord.Guild) -> ml.MessageLogConfig:
        cfg = ml.get_config(guild.id)
        if cfg is None:
            raise ValueError(f"「{guild.name}」還沒有設定訊息紀錄，請先用 `/msglog set`")
        return cfg

    async def guild_autocomplete(
        self, interaction: Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        current = current.lower()
        choices = []
        for guild in self.bot.guilds:
            if current and current not in guild.name.lower() and current not in str(guild.id):
                continue
            mark = "✅ " if ml.get_config(guild.id) else ""
            choices.append(
                app_commands.Choice(name=trim(f"{mark}{guild.name}（{guild.id}）", 100), value=str(guild.id))
            )
        return choices[:25]

    async def channel_autocomplete(
        self, interaction: Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """列出目標伺服器裡機器人能發言的文字頻道

        目標伺服器讀自同一個指令的 guild 參數（namespace），所以先選伺服器、
        再選頻道就能跨伺服器設定。
        """
        try:
            guild = self.resolve_guild(interaction, getattr(interaction.namespace, "guild", None))
        except ValueError:
            return []

        current = current.lower()
        choices = []
        for channel in guild.text_channels:
            if current and current not in channel.name.lower() and current not in str(channel.id):
                continue
            if guild.me is not None:
                perms = channel.permissions_for(guild.me)
                if not (perms.send_messages and perms.embed_links):
                    continue
            choices.append(
                app_commands.Choice(name=trim(f"#{channel.name}", 100), value=str(channel.id))
            )
        return choices[:25]

    async def member_or_channel_autocomplete(
        self, interaction: Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """忽略清單用：同時列出頻道和成員，選哪個都可以"""
        try:
            guild = self.resolve_guild(interaction, getattr(interaction.namespace, "guild", None))
        except ValueError:
            return []

        current = current.lower()
        choices = []
        for channel in guild.channels:
            if len(choices) >= 15:
                break
            if current and current not in channel.name.lower():
                continue
            choices.append(
                app_commands.Choice(name=trim(f"#️⃣ {channel.name}", 100), value=str(channel.id))
            )
        for member in guild.members:
            if len(choices) >= 25:
                break
            if current and current not in member.display_name.lower() and current not in str(member.id):
                continue
            choices.append(
                app_commands.Choice(name=trim(f"👤 {member.display_name}", 100), value=str(member.id))
            )
        return choices[:25]

    def status_embed(self, guild: discord.Guild, cfg: ml.MessageLogConfig) -> discord.Embed:
        if not settings.MESSAGE_LOG_ENABLED:
            state, color = "⛔ 總開關關閉中", discord.Color.dark_grey()
        elif cfg.enabled:
            state, color = "✅ 記錄中", discord.Color.green()
        else:
            state, color = "⏸️ 已停用", discord.Color.orange()

        embed = discord.Embed(title=f"📋 {guild.name} 的訊息紀錄", color=color)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="狀態", value=state, inline=True)
        embed.add_field(name="紀錄頻道", value=f"<#{cfg.channel_id}>", inline=True)
        embed.add_field(
            name="開關",
            value="\n".join(
                f"{'✅' if value else '❌'} {label}" for label, value in cfg.toggle_summary()
            ),
            inline=False,
        )

        if cfg.ignored_channels:
            embed.add_field(
                name=f"忽略的頻道（{len(cfg.ignored_channels)}）",
                value=trim(" ".join(f"<#{c}>" for c in cfg.ignored_channels), FIELD_LIMIT),
                inline=False,
            )
        if cfg.ignored_users:
            embed.add_field(
                name=f"忽略的使用者（{len(cfg.ignored_users)}）",
                value=trim(" ".join(f"<@{u}>" for u in cfg.ignored_users), FIELD_LIMIT),
                inline=False,
            )

        channel = self.bot.get_channel(cfg.channel_id)
        if channel is None:
            embed.add_field(name="⚠️ 警告", value="找不到紀錄頻道，可能已被刪除", inline=False)
        elif guild.me is not None:
            perms = channel.permissions_for(guild.me)
            missing = [
                name
                for name, ok in (("發送訊息", perms.send_messages), ("嵌入連結", perms.embed_links))
                if not ok
            ]
            if missing:
                embed.add_field(
                    name="⚠️ 權限不足", value=f"我在該頻道缺少：{'、'.join(missing)}", inline=False
                )
        if cfg.check_audit_log and guild.me and not guild.me.guild_permissions.view_audit_log:
            embed.add_field(
                name="ℹ️ 提示",
                value="已開啟稽核紀錄查詢，但我沒有「查看稽核日誌」權限，通知不會顯示刪除者",
                inline=False,
            )

        footer = f"伺服器 ID：{guild.id}"
        if cfg.updated_by:
            footer += f"｜最後由 {cfg.updated_by} 更新"
        embed.set_footer(text=footer)
        embed.timestamp = to_dt(cfg.updated_at)
        return embed

    # ------------------------------------------------------------ 指令

    @msglog.command(name="set", description="指定要接收訊息紀錄的頻道（同時啟用）")
    @app_commands.describe(
        channel="通知要送到哪個頻道",
        guild="要設定哪個伺服器，留空就是目前所在的伺服器",
    )
    @dev_only()
    async def set_channel(self, interaction: Interaction, channel: str, guild: str | None = None):
        log(interaction, channel=channel, guild=guild)
        await interaction.response.defer(ephemeral=True)

        try:
            target = self.resolve_guild(interaction, guild)
        except ValueError as e:
            await interaction.followup.send(embed=ui.info_embed(str(e), discord.Color.red()))
            return

        if not channel.strip().isdigit():
            await interaction.followup.send(
                embed=ui.info_embed(f"`{channel}` 不是有效的頻道 ID", discord.Color.red())
            )
            return

        log_channel = target.get_channel(int(channel))
        if log_channel is None or not isinstance(log_channel, discord.TextChannel):
            await interaction.followup.send(
                embed=ui.info_embed(
                    f"在「{target.name}」裡找不到這個文字頻道", discord.Color.red()
                )
            )
            return

        perms = log_channel.permissions_for(target.me) if target.me else None
        if perms is not None and not (perms.send_messages and perms.embed_links):
            await interaction.followup.send(
                embed=ui.info_embed(
                    f"我在 {log_channel.mention} 沒有「發送訊息」或「嵌入連結」權限",
                    discord.Color.red(),
                )
            )
            return

        cfg = ml.get_config(target.id) or ml.MessageLogConfig(
            guild_id=target.id, channel_id=log_channel.id
        )
        cfg.channel_id = log_channel.id
        cfg.enabled = True
        cfg = ml.save_config(cfg, updated_by=interaction.user.id)

        await interaction.followup.send(embed=self.status_embed(target, cfg))

    @msglog.command(name="enable", description="重新啟用某個伺服器的訊息紀錄")
    @app_commands.describe(guild="要啟用哪個伺服器，留空就是目前所在的伺服器")
    @dev_only()
    async def enable(self, interaction: Interaction, guild: str | None = None):
        await self._set_enabled(interaction, guild, True)

    @msglog.command(name="disable", description="暫停某個伺服器的訊息紀錄（設定會保留）")
    @app_commands.describe(guild="要停用哪個伺服器，留空就是目前所在的伺服器")
    @dev_only()
    async def disable(self, interaction: Interaction, guild: str | None = None):
        await self._set_enabled(interaction, guild, False)

    async def _set_enabled(self, interaction: Interaction, guild: str | None, enabled: bool):
        log(interaction, guild=guild, enabled=enabled)
        await interaction.response.defer(ephemeral=True)
        try:
            target = self.resolve_guild(interaction, guild)
            cfg = self.require_config(target)
        except ValueError as e:
            await interaction.followup.send(embed=ui.info_embed(str(e), discord.Color.red()))
            return

        cfg.enabled = enabled
        cfg = ml.save_config(cfg, updated_by=interaction.user.id)
        await interaction.followup.send(embed=self.status_embed(target, cfg))

    @msglog.command(name="config", description="調整訊息紀錄的細項開關（全部留空就是查看目前設定）")
    @app_commands.describe(
        guild="要調整哪個伺服器，留空就是目前所在的伺服器",
        log_delete="訊息被刪除時發送通知",
        log_edit="訊息被編輯時發送通知",
        log_bulk_delete="訊息被整批清除時發送彙整通知",
        ignore_bots="不記錄機器人與 Webhook 的訊息",
        include_content="是否把訊息文字寫進通知",
        log_attachments="是否列出附件、貼圖、投票等內容",
        log_embed_only_edits="連結預覽造成的編輯也要記錄",
        check_audit_log="嘗試從稽核紀錄找出刪除者",
    )
    @dev_only()
    async def config(
        self,
        interaction: Interaction,
        guild: str | None = None,
        log_delete: bool | None = None,
        log_edit: bool | None = None,
        log_bulk_delete: bool | None = None,
        ignore_bots: bool | None = None,
        include_content: bool | None = None,
        log_attachments: bool | None = None,
        log_embed_only_edits: bool | None = None,
        check_audit_log: bool | None = None,
    ):
        log(interaction, guild=guild)
        await interaction.response.defer(ephemeral=True)
        try:
            target = self.resolve_guild(interaction, guild)
            cfg = self.require_config(target)
        except ValueError as e:
            await interaction.followup.send(embed=ui.info_embed(str(e), discord.Color.red()))
            return

        updates = {
            "log_delete": log_delete,
            "log_edit": log_edit,
            "log_bulk_delete": log_bulk_delete,
            "ignore_bots": ignore_bots,
            "include_content": include_content,
            "log_attachments": log_attachments,
            "log_embed_only_edits": log_embed_only_edits,
            "check_audit_log": check_audit_log,
        }
        changed = [key for key, value in updates.items() if value is not None]
        for key in changed:
            setattr(cfg, key, updates[key])

        if changed:
            cfg = ml.save_config(cfg, updated_by=interaction.user.id)

        embed = self.status_embed(target, cfg)
        if changed:
            embed.description = "已更新：" + "、".join(ml.TOGGLES[key][0] for key in changed)
        await interaction.followup.send(embed=embed)

    @msglog.command(name="ignore", description="把頻道或使用者加進／移出忽略清單")
    @app_commands.describe(
        action="要新增還是移除",
        target="頻道或使用者（可用自動補全，也可以直接貼 ID）",
        guild="要調整哪個伺服器，留空就是目前所在的伺服器",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="新增", value="add"),
            app_commands.Choice(name="移除", value="remove"),
            app_commands.Choice(name="清空全部", value="clear"),
        ]
    )
    @dev_only()
    async def ignore(
        self,
        interaction: Interaction,
        action: app_commands.Choice[str],
        target: str | None = None,
        guild: str | None = None,
    ):
        log(interaction, action=action.value, target=target, guild=guild)
        await interaction.response.defer(ephemeral=True)
        try:
            target_guild = self.resolve_guild(interaction, guild)
            cfg = self.require_config(target_guild)
        except ValueError as e:
            await interaction.followup.send(embed=ui.info_embed(str(e), discord.Color.red()))
            return

        if action.value == "clear":
            cfg = ml.set_ignored(cfg.guild_id, channels=[], users=[])
            await interaction.followup.send(embed=self.status_embed(target_guild, cfg))
            return

        raw = (target or "").strip().strip("<>@#&!")
        if not raw.isdigit():
            await interaction.followup.send(
                embed=ui.info_embed(
                    "請指定要忽略的頻道或使用者（可以用自動補全，或直接貼 ID）",
                    discord.Color.red(),
                )
            )
            return

        target_id = int(raw)
        is_channel = target_guild.get_channel_or_thread(target_id) is not None
        current = list(cfg.ignored_channels if is_channel else cfg.ignored_users)
        mention = f"<#{target_id}>" if is_channel else f"<@{target_id}>"

        if action.value == "add":
            if target_id in current:
                await interaction.followup.send(
                    embed=ui.info_embed(f"{mention} 已經在忽略清單裡了", discord.Color.orange())
                )
                return
            current.append(target_id)
        else:
            if target_id not in current:
                await interaction.followup.send(
                    embed=ui.info_embed(f"{mention} 不在忽略清單裡", discord.Color.orange())
                )
                return
            current.remove(target_id)

        cfg = ml.set_ignored(
            cfg.guild_id,
            channels=current if is_channel else None,
            users=None if is_channel else current,
        )
        await interaction.followup.send(embed=self.status_embed(target_guild, cfg))

    @msglog.command(name="status", description="查看某個伺服器的訊息紀錄設定")
    @app_commands.describe(guild="要查看哪個伺服器，留空就是目前所在的伺服器")
    @dev_only()
    async def status(self, interaction: Interaction, guild: str | None = None):
        log(interaction, guild=guild)
        await interaction.response.defer(ephemeral=True)
        try:
            target = self.resolve_guild(interaction, guild)
            cfg = self.require_config(target)
        except ValueError as e:
            await interaction.followup.send(embed=ui.info_embed(str(e), discord.Color.red()))
            return

        await interaction.followup.send(embed=self.status_embed(target, cfg))

    @msglog.command(name="list", description="列出所有設定過訊息紀錄的伺服器")
    @dev_only()
    async def list_configs(self, interaction: Interaction):
        log(interaction)
        await interaction.response.defer(ephemeral=True)

        configs = ml.all_configs()
        if not configs:
            await interaction.followup.send(
                embed=ui.info_embed("目前還沒有任何伺服器設定訊息紀錄")
            )
            return

        embed = discord.Embed(
            title="📚 訊息紀錄一覽",
            description=f"共 {len(configs)} 個伺服器"
            + ("" if settings.MESSAGE_LOG_ENABLED else "\n⛔ **總開關關閉中，目前全部停止記錄**"),
            color=discord.Color.blue(),
        )
        for cfg in configs[:25]:
            guild = self.bot.get_guild(cfg.guild_id)
            name = guild.name if guild else f"（已離開的伺服器 {cfg.guild_id}）"
            events = [
                label
                for label, on in (
                    ("刪除", cfg.log_delete),
                    ("編輯", cfg.log_edit),
                    ("批次刪除", cfg.log_bulk_delete),
                )
                if on
            ]
            embed.add_field(
                name=f"{'✅' if cfg.enabled else '⏸️'} {trim(name, 200)}",
                value=(
                    f"頻道：<#{cfg.channel_id}>\n"
                    f"記錄：{'、'.join(events) or '（沒有開啟任何事件）'}\n"
                    f"ID：`{cfg.guild_id}`"
                ),
                inline=False,
            )
        if len(configs) > 25:
            embed.set_footer(text=f"還有 {len(configs) - 25} 個伺服器沒顯示")
        await interaction.followup.send(embed=embed)

    @msglog.command(name="remove", description="刪除某個伺服器的訊息紀錄設定")
    @app_commands.describe(guild="要刪除哪個伺服器的設定，留空就是目前所在的伺服器")
    @dev_only()
    async def remove(self, interaction: Interaction, guild: str | None = None):
        log(interaction, guild=guild)
        await interaction.response.defer(ephemeral=True)
        try:
            target = self.resolve_guild(interaction, guild)
        except ValueError as e:
            await interaction.followup.send(embed=ui.info_embed(str(e), discord.Color.red()))
            return

        if not ml.delete_config(target.id):
            await interaction.followup.send(
                embed=ui.info_embed(f"「{target.name}」本來就沒有設定", discord.Color.orange())
            )
            return

        await interaction.followup.send(
            embed=ui.info_embed(f"✅ 已刪除「{target.name}」的訊息紀錄設定")
        )

    @msglog.command(name="test", description="送一則範例通知到紀錄頻道，確認權限與外觀")
    @app_commands.describe(guild="要測試哪個伺服器，留空就是目前所在的伺服器")
    @dev_only()
    async def test(self, interaction: Interaction, guild: str | None = None):
        log(interaction, guild=guild)
        await interaction.response.defer(ephemeral=True)
        try:
            target = self.resolve_guild(interaction, guild)
            cfg = self.require_config(target)
        except ValueError as e:
            await interaction.followup.send(embed=ui.info_embed(str(e), discord.Color.red()))
            return

        embed = discord.Embed(
            title="🗑️ 訊息被刪除",
            color=COLOR_DELETE,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        embed.add_field(
            name="作者", value=f"{interaction.user.mention}（`{interaction.user.id}`）", inline=True
        )
        embed.add_field(name="頻道", value=f"<#{cfg.channel_id}>", inline=True)
        embed.add_field(name="內容", value="這是一則測試通知，實際的通知長這樣。", inline=False)
        embed.add_field(name="附件（1）", value="🖼️ [example.png](https://discord.com) — 12.3 KB", inline=False)
        embed.set_footer(text="訊息 ID：000000000000000000")

        if await self.send_log(cfg, embed):
            await interaction.followup.send(
                embed=ui.info_embed(f"✅ 已送出測試通知到 <#{cfg.channel_id}>")
            )
        else:
            await interaction.followup.send(
                embed=ui.info_embed(
                    f"送不出去，請確認我在 <#{cfg.channel_id}> 有「發送訊息」與「嵌入連結」權限",
                    discord.Color.red(),
                )
            )

    # 自動補全綁定（放在指令都定義完之後）
    set_channel.autocomplete("guild")(guild_autocomplete)
    set_channel.autocomplete("channel")(channel_autocomplete)
    enable.autocomplete("guild")(guild_autocomplete)
    disable.autocomplete("guild")(guild_autocomplete)
    config.autocomplete("guild")(guild_autocomplete)
    status.autocomplete("guild")(guild_autocomplete)
    remove.autocomplete("guild")(guild_autocomplete)
    test.autocomplete("guild")(guild_autocomplete)
    ignore.autocomplete("guild")(guild_autocomplete)
    ignore.autocomplete("target")(member_or_channel_autocomplete)

    # ------------------------------------------------------------ 錯誤處理

    async def cog_app_command_error(
        self, interaction: Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, NotDeveloper):
            message = "這是開發者專用指令，你沒有權限使用"
        elif isinstance(error, FeatureDisabled):
            message = (
                "訊息紀錄的總開關目前是關閉的\n"
                "請把 `.env` 裡的 `MESSAGE_LOG_ENABLED` 設成 `true` 後重啟機器人"
            )
        else:
            logging.error(f"訊息紀錄指令發生錯誤: {error}", exc_info=error)
            message = f"發生未預期的錯誤：{error}"

        embed = ui.info_embed(message, discord.Color.red())
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(MessageLogCog(bot))
    if not settings.MESSAGE_LOG_ENABLED:
        logging.info("訊息紀錄總開關關閉中 (MESSAGE_LOG_ENABLED=false)")
    logging.info(f'{__name__} 已載入')
