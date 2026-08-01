import asyncio
import datetime
import logging
import re
from dataclasses import dataclass

import discord
from discord import Interaction, app_commands
from discord.ext import commands, tasks

import settings
from utils import notify as nt
from utils import ui
from utils.log import log
from utils.timeparse import parse_target

READ_HINT = "📌 看到訊息請按下方的「已讀」按鈕"


@dataclass
class SendOptions:
    """一次群發的各種選項，一路從指令傳到 start_broadcast"""

    channel: discord.abc.GuildChannel | None = None
    anonymous: bool = False
    scheduled_at: datetime.datetime | None = None
    remind_every: float = 0
    remind_max: int = 3


def build_embed(
    bc: nt.Broadcast,
    guild: discord.Guild | None,
    author: discord.User | discord.Member | None,
    *,
    read_at=None,
) -> discord.Embed:
    """組出私訊裡看到的通知內容"""
    lines = [bc.content]

    if bc.channel_id and guild:
        channel = guild.get_channel(bc.channel_id)
        if channel:
            lines.append(channel.mention)

    embed = discord.Embed(
        title=bc.title,
        description="\n".join(lines)[:4096],
        color=discord.Color.green() if read_at else discord.Color.blue(),
    )

    # footer 不會渲染提及，所以發布者只能用顯示名稱，改放頭像當圖示
    status = f"✅ 你已於 {read_at:%Y-%m-%d %H:%M} 確認已讀" if read_at else READ_HINT

    if bc.anonymous:
        # 匿名時連頭像也要換掉，否則等於直接指出是誰發的
        signature = guild.name if guild else ""
        icon_url = guild.icon.url if guild and guild.icon else None
    else:
        signature = f"發布者:{author.display_name if author else '未知'}"
        if guild:
            signature += f"｜{guild.name}"
        icon_url = author.display_avatar.url if author else None

    embed.set_footer(
        text=f"{status}｜{signature}"[:2048] if signature else status[:2048],
        icon_url=icon_url,
    )
    return embed


class ReadButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"notify:read:(?P<bid>[0-9]+)",
):
    """已讀按鈕

    用 DynamicItem 把群發編號編進 custom_id，機器人重啟後按鈕依然有效。
    """

    def __init__(self, bid: int):
        self.bid = bid
        super().__init__(
            discord.ui.Button(
                label="已讀",
                emoji="✅",
                style=discord.ButtonStyle.success,
                custom_id=f"notify:read:{bid}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction: Interaction, item, match: re.Match):
        return cls(int(match["bid"]))

    async def callback(self, interaction: Interaction):
        cog: "NotifyCog" = interaction.client.get_cog("NotifyCog")
        if cog is None:
            await interaction.response.send_message(
                "通知模組尚未載入，請稍後再試", ephemeral=True
            )
            return

        if nt.get_broadcast(self.bid) is None:
            await interaction.response.send_message(
                "找不到這則通知的紀錄", ephemeral=True
            )
            return

        if nt.mark_read(self.bid, interaction.user.id):
            log(interaction, broadcast=self.bid, action="已讀")

        # 重新讀一次才拿得到剛寫入的已讀時間
        bc = nt.get_broadcast(self.bid)

        guild = interaction.client.get_guild(bc.guild_id)
        author = interaction.client.get_user(bc.author_id)
        embed = build_embed(bc, guild, author, read_at=bc.read_at(interaction.user.id))

        # 把按鈕換成已按下的樣子
        view = discord.ui.View(timeout=None)
        done = discord.ui.Button(
            label="已讀", emoji="✅", style=discord.ButtonStyle.success, disabled=True
        )
        view.add_item(done)
        await interaction.response.edit_message(embed=embed, view=view)


class BroadcastModal(discord.ui.Modal, title="群發通知"):
    notice_title = discord.ui.TextInput(
        label="標題",
        max_length=256,
    )
    notice_content = discord.ui.TextInput(
        label="內容",
        style=discord.TextStyle.paragraph,
        max_length=2000,
    )

    def __init__(
        self,
        cog: "NotifyCog",
        *,
        role: discord.Role | None = None,
        members: list[discord.Member] | None = None,
        opts: SendOptions,
    ):
        super().__init__()
        self.cog = cog
        self.role = role
        self.members = members
        self.opts = opts

    async def on_submit(self, interaction: Interaction):
        await self.cog.start_broadcast(
            interaction,
            role=self.role,
            members=self.members,
            opts=self.opts,
            title=str(self.notice_title),
            content=str(self.notice_content),
        )


class MemberSelect(discord.ui.UserSelect):
    """挑人用的選單，選完直接接著跳出填寫視窗"""

    def __init__(self, cog: "NotifyCog", opts: SendOptions):
        super().__init__(
            placeholder="選擇要通知的成員（可多選，最多 25 人）",
            min_values=1,
            max_values=25,
        )
        self.cog = cog
        self.opts = opts

    async def callback(self, interaction: Interaction):
        members = [u for u in self.values if not u.bot]
        if not members:
            await interaction.response.send_message(
                embed=ui.info_embed("選到的都是機器人，請重新選擇", discord.Color.red()),
                ephemeral=True,
            )
            return
        log(interaction, selected=len(members))
        await interaction.response.send_modal(
            BroadcastModal(self.cog, members=members, opts=self.opts)
        )


class MemberSelectView(discord.ui.View):
    def __init__(self, cog: "NotifyCog", opts: SendOptions):
        super().__init__(timeout=180)
        self.add_item(MemberSelect(cog, opts))


def mention_list(user_ids: list[int], limit: int = 20) -> str:
    """把一串使用者 ID 轉成提及，太多就省略，避免超過欄位長度上限"""
    if not user_ids:
        return "—"
    shown = " ".join(f"<@{uid}>" for uid in user_ids[:limit])
    if len(user_ids) > limit:
        shown += f" …等 {len(user_ids)} 人"
    return shown[:1024]


def target_text(bc: nt.Broadcast) -> str:
    """群發對象的顯示文字 — 身分組模式顯示提及，個人模式顯示人數"""
    if bc.is_role_broadcast:
        return f"<@&{bc.role_id}>（{len(bc.targets)} 人）"
    return f"指定成員 {len(bc.targets)} 人"


class NotifyCog(commands.Cog):
    notify = app_commands.Group(
        name="notify",
        description="群發私訊通知並追蹤已讀狀況",
        guild_only=True,
        # None 代表不限制，所有人都看得到、用得到
        default_permissions=(
            discord.Permissions(manage_guild=True)
            if settings.NOTIFY_REQUIRE_PERMISSION
            else None
        ),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def interaction_check(self, interaction: Interaction) -> bool:
        """再擋一次權限

        default_permissions 只是 Discord 端的「預設」，伺服器管理員可以在
        設定 → 整合 裡覆寫，所以這裡自己再檢查一次，兩邊都要過才放行。
        """
        if not settings.NOTIFY_REQUIRE_PERMISSION:
            return True
        if interaction.user.guild_permissions.manage_guild:
            return True
        raise app_commands.MissingPermissions(["manage_guild"])

    async def cog_load(self):
        self.bot.add_dynamic_items(ReadButton)
        self.scheduler_loop.start()

    async def cog_unload(self):
        self.bot.remove_dynamic_items(ReadButton)
        self.scheduler_loop.cancel()

    def get_broadcast(self, bid: int, guild_id: int) -> nt.Broadcast | None:
        """取單筆，順便擋掉跨伺服器查詢別人的通知"""
        bc = nt.get_broadcast(bid)
        if bc is None or bc.guild_id != guild_id:
            return None
        return bc

    # ------------------------------------------------------------ 寄送

    async def resolve_members(self, bc: nt.Broadcast) -> list[discord.Member]:
        """把 role_id / extra_ids 展開成實際的成員清單

        排程通知等到寄送當下才解析，身分組中途增減人也能反映到。
        用 id 當 key 去重 — 有人既在身分組裡、又被單獨指定時只會收到一封。
        """
        guild = self.bot.get_guild(bc.guild_id)
        if guild is None:
            return []
        if not guild.chunked:
            await guild.chunk()

        resolved: dict[int, discord.Member] = {}
        if bc.role_id:
            role = guild.get_role(bc.role_id)
            if role:
                for m in role.members:
                    if not m.bot:
                        resolved[m.id] = m
        for uid in bc.extra_ids:
            m = guild.get_member(uid)
            if m and not m.bot:
                resolved[m.id] = m
        return list(resolved.values())

    async def dispatch(self, bc: nt.Broadcast) -> tuple[int, int]:
        """實際寄出一則通知（立即與排程共用這條路徑）"""
        members = await self.resolve_members(bc)
        nt.set_recipients(bc.id, [m.id for m in members])
        bc.sent_at = nt.now().isoformat()
        nt.update_broadcast(bc)

        return await self.deliver(bc, members)

    async def deliver(
        self, bc: nt.Broadcast, members: list[discord.Member]
    ) -> tuple[int, int]:
        """逐一私訊，回傳 (成功, 失敗) 人數

        每封之間留間隔，避免觸發 Discord 的私訊速率限制。
        """
        guild = self.bot.get_guild(bc.guild_id)
        author = self.bot.get_user(bc.author_id)
        sent = failed = 0

        for member in members:
            view = discord.ui.View(timeout=None)
            view.add_item(ReadButton(bc.id))
            try:
                await member.send(embed=build_embed(bc, guild, author), view=view)
            except discord.Forbidden:
                failed += 1
                nt.set_failed(bc.id, member.id, True)
                logging.info(f"無法私訊 {member} (關閉私訊或已封鎖機器人)")
            except discord.HTTPException as e:
                failed += 1
                nt.set_failed(bc.id, member.id, True)
                logging.warning(f"私訊 {member} 失敗: {e}")
            else:
                sent += 1
                # 上次送不出去、這次通了的話要把標記清掉
                nt.set_failed(bc.id, member.id, False)
            await asyncio.sleep(settings.NOTIFY_SEND_DELAY)

        return sent, failed

    async def send_reminder(self, bc: nt.Broadcast) -> tuple[int, int]:
        """自動提醒還沒按已讀的人（含上次私訊失敗的，會一併重試）"""
        guild = self.bot.get_guild(bc.guild_id)
        if guild is None:
            return 0, 0

        members = [m for uid in bc.pending_ids if (m := guild.get_member(uid))]
        # 記次數與時間要在寄送前更新，避免寄送期間又被下一輪掃到
        bc.remind_count += 1
        bc.last_remind_at = nt.now().isoformat()
        nt.update_broadcast(bc)

        if not members:
            return 0, 0
        sent, failed = await self.deliver(bc, members)
        logging.info(
            f"通知 #{bc.id} 自動提醒第 {bc.remind_count}/{bc.remind_max} 次："
            f"{sent} 人送達、{failed} 人失敗"
        )
        return sent, failed

    # 一分鐘一次：檢查排程是否到期、以及有沒有該自動提醒的通知。
    # 迴圈會等前一輪跑完才進下一輪，所以寄送花很久也不會重疊觸發。
    @tasks.loop(minutes=1)
    async def scheduler_loop(self):
        at = nt.now()

        for bc in nt.all_broadcasts():
            if not bc.is_due(at):
                continue
            try:
                sent, failed = await self.dispatch(bc)
                logging.info(
                    f"排程通知 #{bc.id}「{bc.title}」已寄出：{sent} 人送達、{failed} 人失敗"
                )
            except Exception as e:
                logging.error(f"排程通知 #{bc.id} 寄送失敗: {e}", exc_info=e)

        for bc in nt.all_broadcasts():
            if not bc.needs_remind(at):
                continue
            try:
                await self.send_reminder(bc)
            except Exception as e:
                logging.error(f"通知 #{bc.id} 自動提醒失敗: {e}", exc_info=e)

    @scheduler_loop.before_loop
    async def before_scheduler_loop(self):
        await self.bot.wait_until_ready()

    async def start_broadcast(
        self,
        interaction: Interaction,
        *,
        role: discord.Role | None = None,
        members: list[discord.Member] | None = None,
        opts: SendOptions,
        title: str,
        content: str,
    ):
        """role 和 members 可以並用，重複的人只會收到一封"""
        await interaction.response.defer(ephemeral=True)

        bc = nt.Broadcast(
            guild_id=interaction.guild_id,
            role_id=role.id if role is not None else None,
            extra_ids=[m.id for m in (members or []) if not m.bot],
            author_id=interaction.user.id,
            title=title,
            content=content,
            created_at=nt.now().isoformat(),
            channel_id=opts.channel.id if opts.channel else None,
            anonymous=opts.anonymous,
            scheduled_at=opts.scheduled_at.isoformat() if opts.scheduled_at else None,
            remind_every_hours=opts.remind_every,
            remind_max=opts.remind_max,
        )

        # 先試算一次，確認真的有人收得到；排程的到時候還會重新解析一遍
        preview = await self.resolve_members(bc)
        if not preview:
            await interaction.followup.send(
                embed=ui.info_embed(
                    f"{role.mention} 裡沒有可私訊的成員"
                    if role is not None
                    else "沒有可私訊的成員",
                    discord.Color.red(),
                )
            )
            return

        extra = len(bc.extra_ids)
        if role is not None:
            target_desc = role.mention + (f" + {extra} 位指定成員" if extra else "")
        else:
            target_desc = f"{len(preview)} 位指定成員"

        bc = nt.create_broadcast(bc)

        notes = []
        if opts.anonymous:
            notes.append("🕵️ 匿名寄送")
        if opts.remind_every > 0:
            notes.append(
                f"🔔 每 {opts.remind_every:g} 小時自動提醒未讀，最多 {opts.remind_max} 次"
            )
        note_text = ("\n" + "\n".join(notes)) if notes else ""

        # 排程的話存起來就好，交給 scheduler_loop 到時間再寄
        if opts.scheduled_at is not None:
            log(interaction, broadcast=bc.id, scheduled=bc.scheduled_at)
            embed = discord.Embed(
                title=f"⏰ 已排程 — #{bc.id}",
                description=f"**{bc.title}**",
                color=discord.Color.orange(),
            )
            embed.add_field(name="對象", value=target_desc + note_text, inline=True)
            embed.add_field(
                name="預定寄送",
                value=f"{opts.scheduled_at:%Y-%m-%d %H:%M}\n({settings.TIMEZONE})",
                inline=True,
            )
            embed.add_field(name="目前人數", value=f"約 {len(preview)} 人", inline=True)
            embed.set_footer(text=f"要取消就用 /notify delete id:{bc.id}")
            await interaction.followup.send(embed=embed)
            return

        estimate = int(len(preview) * settings.NOTIFY_SEND_DELAY)
        await interaction.followup.send(
            embed=ui.info_embed(
                f"📨 開始群發給 {target_desc}（共 {len(preview)} 人）…\n"
                f"預計需要約 {estimate} 秒，完成後會回報結果"
            )
        )

        sent, failed = await self.dispatch(bc)
        log(interaction, broadcast=bc.id, sent=sent, failed=failed)

        # 寄送過程的成敗是逐列寫進資料庫的，手上這份已經過時了
        bc = nt.get_broadcast(bc.id) or bc

        embed = discord.Embed(
            title=f"✅ 群發完成 — #{bc.id}",
            description=f"**{bc.title}**",
            color=discord.Color.green(),
        )
        embed.add_field(name="對象", value=target_desc + note_text, inline=True)
        embed.add_field(name="成功送達", value=f"{sent} 人", inline=True)
        embed.add_field(name="私訊失敗", value=f"{failed} 人", inline=True)
        if bc.failed:
            embed.add_field(
                name="⚠️ 收不到的人", value=mention_list(bc.failed), inline=False
            )
        embed.set_footer(text=f"用 /notify status id:{bc.id} 查看已讀狀況")
        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------ 指令

    @notify.command(
        name="send", description="群發私訊通知（都不指定對象時會出現成員選單）"
    )
    @app_commands.describe(
        role="要通知的身分組 (選填)",
        user="要額外通知的單一成員 (選填，可與身分組並用)",
        channel="要附在通知裡的頻道連結 (選填)",
        anonymous="隱藏寄送者，收件者看不到是誰發的 (預設關閉)",
        schedule="排程寄送時間，例如 2025-01-01 09:00 (選填，不填就立刻寄)",
        remind_every="每隔幾小時自動提醒未讀的人 (選填，不填就不提醒)",
        remind_max="自動提醒最多幾次 (預設 3)",
    )
    async def send(
        self,
        interaction: Interaction,
        role: discord.Role | None = None,
        user: discord.Member | None = None,
        channel: discord.abc.GuildChannel | None = None,
        anonymous: bool = False,
        schedule: str | None = None,
        remind_every: app_commands.Range[float, 0.1, 720.0] | None = None,
        remind_max: app_commands.Range[int, 1, 20] = 3,
    ):
        log(
            interaction,
            role=role.name if role else None,
            user=str(user) if user else None,
            anonymous=anonymous,
            schedule=schedule,
        )

        scheduled_at = None
        if schedule:
            try:
                scheduled_at = parse_target(schedule)
            except ValueError as e:
                await interaction.response.send_message(
                    embed=ui.info_embed(str(e), discord.Color.red()), ephemeral=True
                )
                return
            if scheduled_at <= nt.now():
                await interaction.response.send_message(
                    embed=ui.info_embed(
                        f"排程時間 {scheduled_at:%Y-%m-%d %H:%M} 已經過了，請填未來的時間",
                        discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return

        opts = SendOptions(
            channel=channel,
            anonymous=anonymous,
            scheduled_at=scheduled_at,
            remind_every=remind_every or 0,
            remind_max=remind_max,
        )

        if role is None and user is None:
            # 沒指定對象 → 先挑人，選單的 callback 再接著跳出填寫視窗
            await interaction.response.send_message(
                embed=ui.info_embed(
                    "請選擇要通知的成員，選完會跳出填寫標題與內容的視窗"
                ),
                view=MemberSelectView(self, opts),
                ephemeral=True,
            )
            return

        # 用 Modal 才能輸入多行內容
        await interaction.response.send_modal(
            BroadcastModal(
                self, role=role, members=[user] if user else None, opts=opts
            )
        )

    @notify.command(
        name="autoremind", description="事後調整或關閉某則通知的自動提醒"
    )
    @app_commands.describe(
        id="群發編號",
        every_hours="每隔幾小時提醒一次未讀的人，填 0 關閉",
        max_times="最多再提醒幾次 (預設 3)",
    )
    async def autoremind(
        self,
        interaction: Interaction,
        id: int,
        every_hours: app_commands.Range[float, 0.0, 720.0],
        max_times: app_commands.Range[int, 1, 20] = 3,
    ):
        log(interaction, broadcast=id, every_hours=every_hours, max_times=max_times)

        bc = self.get_broadcast(id, interaction.guild_id)
        if bc is None:
            await interaction.response.send_message(
                embed=ui.info_embed(f"找不到編號 #{id} 的群發紀錄", discord.Color.red()),
                ephemeral=True,
            )
            return

        bc.remind_every_hours = every_hours
        bc.remind_max = max_times
        # 重新設定就把已提醒次數歸零，不然改完可能立刻就用完額度
        bc.remind_count = 0
        bc.last_remind_at = None
        bc = nt.update_broadcast(bc)

        if every_hours <= 0:
            await interaction.response.send_message(
                embed=ui.info_embed(f"🔕 已關閉 #{id}「{bc.title}」的自動提醒"),
                ephemeral=True,
            )
            return

        nxt = bc.next_remind_dt
        await interaction.response.send_message(
            embed=ui.info_embed(
                f"🔔 #{id}「{bc.title}」已設定每 {every_hours:g} 小時提醒未讀的人，"
                f"最多 {max_times} 次\n"
                + (
                    f"下次提醒：{nxt:%Y-%m-%d %H:%M}"
                    if nxt
                    else "（這則還在排程中，寄出後才會開始計時）"
                )
            ),
            ephemeral=True,
        )

    @notify.command(name="status", description="查看某則通知誰已讀、誰還沒")
    @app_commands.describe(id="群發編號，可用 /notify list 查看")
    async def status(self, interaction: Interaction, id: int):
        log(interaction, broadcast=id)
        await interaction.response.defer(ephemeral=True)

        bc = self.get_broadcast(id, interaction.guild_id)
        if bc is None:
            await interaction.followup.send(
                embed=ui.info_embed(f"找不到編號 #{id} 的群發紀錄", discord.Color.red())
            )
            return

        # 還沒寄出的排程通知沒有已讀資料可看，改顯示排程資訊
        if bc.is_pending:
            embed = discord.Embed(
                title=f"⏰ 排程中 — #{bc.id}",
                description=f"**{bc.title}**\n\n{bc.content}"[:4096],
                color=discord.Color.orange(),
            )
            embed.add_field(name="對象", value=target_text(bc), inline=True)
            embed.add_field(
                name="發布者",
                value=f"<@{bc.author_id}>" + ("\n🕵️ 匿名寄送" if bc.anonymous else ""),
                inline=True,
            )
            embed.add_field(
                name="預定寄送",
                value=f"{bc.scheduled_dt:%Y-%m-%d %H:%M}\n({settings.TIMEZONE})",
                inline=True,
            )
            if bc.auto_remind_on:
                embed.add_field(
                    name="自動提醒",
                    value=f"寄出後每 {bc.remind_every_hours:g} 小時提醒未讀的人，"
                    f"最多 {bc.remind_max} 次",
                    inline=False,
                )
            embed.set_footer(text=f"要取消就用 /notify delete id:{bc.id}")
            await interaction.followup.send(embed=embed)
            return

        read, unread = bc.read_ids, bc.unread_ids
        filled = int(bc.progress * 10)
        bar = "█" * filled + "░" * (10 - filled)

        embed = discord.Embed(
            title=f"📊 已讀狀況 — #{bc.id}",
            description=f"**{bc.title}**\n`{bar}` {len(read)}/{len(bc.delivered)} ({bc.progress:.0%})",
            color=discord.Color.blue(),
        )
        embed.add_field(name="對象", value=target_text(bc), inline=True)
        embed.add_field(
            name="發布者",
            value=f"<@{bc.author_id}>" + ("\n🕵️ 匿名寄送" if bc.anonymous else ""),
            inline=True,
        )
        embed.add_field(
            name="寄送時間", value=f"{bc.sent_dt:%Y-%m-%d %H:%M}", inline=True
        )
        if bc.auto_remind_on:
            nxt = bc.next_remind_dt
            embed.add_field(
                name="🔔 自動提醒",
                value=f"每 {bc.remind_every_hours:g} 小時・已提醒 "
                f"{bc.remind_count}/{bc.remind_max} 次\n"
                + (f"下次：{nxt:%Y-%m-%d %H:%M}" if nxt else "已達次數上限"),
                inline=False,
            )
        embed.add_field(
            name=f"✅ 已讀 ({len(read)})", value=mention_list(read), inline=False
        )
        embed.add_field(
            name=f"⬜ 未讀 ({len(unread)})", value=mention_list(unread), inline=False
        )
        if bc.failed:
            embed.add_field(
                name=f"⚠️ 私訊失敗 ({len(bc.failed)})",
                value=mention_list(bc.failed),
                inline=False,
            )
        embed.set_footer(text=f"用 /notify remind id:{bc.id} 提醒未讀的人")
        await interaction.followup.send(embed=embed)

    @notify.command(name="list", description="列出這個伺服器最近的群發紀錄")
    async def list_broadcasts(self, interaction: Interaction):
        log(interaction)
        entries = nt.guild_broadcasts(interaction.guild_id)
        if not entries:
            await interaction.response.send_message(
                embed=ui.info_embed("這個伺服器還沒有群發紀錄"), ephemeral=True
            )
            return

        entries.sort(key=lambda b: b.id, reverse=True)
        embed = discord.Embed(title="📋 群發紀錄", color=discord.Color.blue())
        for bc in entries[:10]:
            if bc.is_pending:
                line = f"{target_text(bc)}\n⏰ 預定 {bc.scheduled_dt:%Y-%m-%d %H:%M} 寄出"
            else:
                line = (
                    f"{target_text(bc)} ・ {bc.sent_dt:%Y-%m-%d %H:%M}\n"
                    f"已讀 {len(bc.read_ids)}/{len(bc.delivered)} ({bc.progress:.0%})"
                )
                if bc.auto_remind_on:
                    line += f"\n🔔 自動提醒 {bc.remind_count}/{bc.remind_max} 次"
            embed.add_field(name=f"#{bc.id} {bc.title}", value=line, inline=False)
        embed.set_footer(text=f"共 {len(entries)} 筆，顯示最新 10 筆")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @notify.command(name="remind", description="再私訊提醒一次還沒按已讀的人")
    @app_commands.describe(id="群發編號")
    async def remind(self, interaction: Interaction, id: int):
        log(interaction, broadcast=id)
        await interaction.response.defer(ephemeral=True)

        bc = self.get_broadcast(id, interaction.guild_id)
        if bc is None:
            await interaction.followup.send(
                embed=ui.info_embed(f"找不到編號 #{id} 的群發紀錄", discord.Color.red())
            )
            return

        # 上次私訊失敗的人也一起重試
        pending = bc.pending_ids
        if not pending:
            await interaction.followup.send(
                embed=ui.info_embed("🎉 所有人都已經按過已讀了")
            )
            return

        members = [m for uid in pending if (m := interaction.guild.get_member(uid))]
        if not members:
            await interaction.followup.send(
                embed=ui.info_embed(
                    "還沒確認的人都已經不在伺服器裡了", discord.Color.orange()
                )
            )
            return

        await interaction.followup.send(
            embed=ui.info_embed(f"📨 正在提醒 {len(members)} 位還沒按已讀的成員…")
        )

        sent, failed = await self.deliver(bc, members)

        await interaction.followup.send(
            embed=ui.info_embed(
                f"✅ 已提醒 {sent} 人" + (f"，{failed} 人收不到私訊" if failed else "")
            )
        )

    @notify.command(name="delete", description="刪除群發紀錄 (已送出的私訊不會消失)")
    @app_commands.describe(id="群發編號")
    async def delete(self, interaction: Interaction, id: int):
        log(interaction, broadcast=id)
        bc = self.get_broadcast(id, interaction.guild_id)
        if bc is None:
            await interaction.response.send_message(
                embed=ui.info_embed(
                    f"找不到編號 #{id} 的群發紀錄", discord.Color.red()
                ),
                ephemeral=True,
            )
            return

        nt.delete_broadcast(id)
        await interaction.response.send_message(
            embed=ui.info_embed(f"🗑️ 已刪除 #{id}「{bc.title}」的紀錄"), ephemeral=True
        )

    # ------------------------------------------------------------ 錯誤處理

    async def cog_app_command_error(
        self, interaction: Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, app_commands.MissingPermissions):
            message = "你需要「管理伺服器」權限才能使用這個指令"
        else:
            logging.error(f"群發指令發生錯誤: {error}", exc_info=error)
            message = f"發生未預期的錯誤：{error}"

        embed = ui.info_embed(message, discord.Color.red())
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(NotifyCog(bot))
    logging.info(f"{__name__} 已載入")
