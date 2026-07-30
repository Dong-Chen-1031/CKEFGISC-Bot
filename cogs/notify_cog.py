import asyncio
import logging
import re

import discord
from discord import Interaction, app_commands
from discord.ext import commands

import settings
from utils import notify as nt
from utils import ui
from utils.log import log

READ_HINT = "📌 看到訊息請按下方的「已讀」按鈕"


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

        bc = cog.broadcasts.get(self.bid)
        if bc is None:
            await interaction.response.send_message(
                "找不到這則通知的紀錄", ephemeral=True
            )
            return

        first_time = bc.mark_read(interaction.user.id)
        if first_time:
            cog.save()
            log(interaction, broadcast=self.bid, action="已讀")

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
        channel: discord.abc.GuildChannel | None = None,
        anonymous: bool = False,
    ):
        super().__init__()
        self.cog = cog
        self.role = role
        self.members = members
        self.channel = channel
        self.anonymous = anonymous

    async def on_submit(self, interaction: Interaction):
        await self.cog.start_broadcast(
            interaction,
            role=self.role,
            members=self.members,
            channel=self.channel,
            anonymous=self.anonymous,
            title=str(self.notice_title),
            content=str(self.notice_content),
        )


class MemberSelect(discord.ui.UserSelect):
    """挑人用的選單，選完直接接著跳出填寫視窗"""

    def __init__(
        self,
        cog: "NotifyCog",
        channel: discord.abc.GuildChannel | None,
        anonymous: bool = False,
    ):
        super().__init__(
            placeholder="選擇要通知的成員（可多選，最多 25 人）",
            min_values=1,
            max_values=25,
        )
        self.cog = cog
        self.channel = channel
        self.anonymous = anonymous

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
            BroadcastModal(
                self.cog,
                members=members,
                channel=self.channel,
                anonymous=self.anonymous,
            )
        )


class MemberSelectView(discord.ui.View):
    def __init__(
        self,
        cog: "NotifyCog",
        channel: discord.abc.GuildChannel | None,
        anonymous: bool = False,
    ):
        super().__init__(timeout=180)
        self.add_item(MemberSelect(cog, channel, anonymous))


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
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.broadcasts: dict[int, nt.Broadcast] = nt.load_all()

    async def cog_load(self):
        self.bot.add_dynamic_items(ReadButton)

    async def cog_unload(self):
        self.bot.remove_dynamic_items(ReadButton)

    def save(self):
        nt.save_all(self.broadcasts)

    def get_broadcast(self, bid: int, guild_id: int) -> nt.Broadcast | None:
        bc = self.broadcasts.get(bid)
        if bc is None or bc.guild_id != guild_id:
            return None
        return bc

    # ------------------------------------------------------------ 寄送

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
                if member.id not in bc.failed:
                    bc.failed.append(member.id)
                logging.info(f"無法私訊 {member} (關閉私訊或已封鎖機器人)")
            except discord.HTTPException as e:
                failed += 1
                if member.id not in bc.failed:
                    bc.failed.append(member.id)
                logging.warning(f"私訊 {member} 失敗: {e}")
            else:
                sent += 1
                if member.id in bc.failed:
                    # 上次送不出去，這次通了
                    bc.failed.remove(member.id)
            await asyncio.sleep(settings.NOTIFY_SEND_DELAY)

        return sent, failed

    async def start_broadcast(
        self,
        interaction: Interaction,
        *,
        role: discord.Role | None = None,
        members: list[discord.Member] | None = None,
        channel: discord.abc.GuildChannel | None,
        anonymous: bool = False,
        title: str,
        content: str,
    ):
        """role 和 members 二選一：給 role 就發給整組，給 members 就只發給那些人"""
        await interaction.response.defer(ephemeral=True)

        if role is not None:
            # role.members 需要完整的成員快取
            if not interaction.guild.chunked:
                await interaction.guild.chunk()

        # 用 id 當 key 去重 — 有人既在身分組裡、又被單獨指定時只會收到一封
        resolved: dict[int, discord.Member] = {}
        role_ids: set[int] = set()
        if role is not None:
            for m in role.members:
                if not m.bot:
                    resolved[m.id] = m
                    role_ids.add(m.id)
        for m in members or []:
            if not m.bot:
                resolved[m.id] = m

        members = list(resolved.values())
        if not members:
            await interaction.followup.send(
                embed=ui.info_embed(
                    f"{role.mention} 裡沒有可私訊的成員"
                    if role is not None
                    else "沒有可私訊的成員",
                    discord.Color.red(),
                )
            )
            return

        extra = len(resolved) - len(role_ids)
        if role is not None:
            target_desc = role.mention + (f" + {extra} 位指定成員" if extra else "")
        else:
            target_desc = f"{len(members)} 位指定成員"

        bc = nt.Broadcast(
            id=nt.next_id(self.broadcasts),
            guild_id=interaction.guild_id,
            role_id=role.id if role is not None else None,
            author_id=interaction.user.id,
            title=title,
            content=content,
            created_at=nt.now().isoformat(),
            channel_id=channel.id if channel else None,
            anonymous=anonymous,
            targets=[m.id for m in members],
        )
        self.broadcasts[bc.id] = bc
        self.save()

        estimate = int(len(members) * settings.NOTIFY_SEND_DELAY)
        await interaction.followup.send(
            embed=ui.info_embed(
                f"📨 開始群發給 {target_desc}（共 {len(members)} 人）…\n"
                f"預計需要約 {estimate} 秒，完成後會回報結果"
            )
        )

        sent, failed = await self.deliver(bc, members)
        self.save()
        log(interaction, broadcast=bc.id, sent=sent, failed=failed)

        embed = discord.Embed(
            title=f"✅ 群發完成 — #{bc.id}",
            description=f"**{bc.title}**",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="對象",
            value=target_desc + ("\n🕵️ 匿名寄送" if anonymous else ""),
            inline=True,
        )
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
    )
    async def send(
        self,
        interaction: Interaction,
        role: discord.Role | None = None,
        user: discord.Member | None = None,
        channel: discord.abc.GuildChannel | None = None,
        anonymous: bool = False,
    ):
        log(
            interaction,
            role=role.name if role else None,
            user=str(user) if user else None,
            anonymous=anonymous,
        )

        if role is None and user is None:
            # 沒指定對象 → 先挑人，選單的 callback 再接著跳出填寫視窗
            await interaction.response.send_message(
                embed=ui.info_embed(
                    "請選擇要通知的成員，選完會跳出填寫標題與內容的視窗"
                ),
                view=MemberSelectView(self, channel, anonymous),
                ephemeral=True,
            )
            return

        # 用 Modal 才能輸入多行內容
        await interaction.response.send_modal(
            BroadcastModal(
                self,
                role=role,
                members=[user] if user else None,
                channel=channel,
                anonymous=anonymous,
            )
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
            name="發布時間", value=f"{bc.created_dt:%Y-%m-%d %H:%M}", inline=True
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
        entries = [
            b for b in self.broadcasts.values() if b.guild_id == interaction.guild_id
        ]
        if not entries:
            await interaction.response.send_message(
                embed=ui.info_embed("這個伺服器還沒有群發紀錄"), ephemeral=True
            )
            return

        entries.sort(key=lambda b: b.id, reverse=True)
        embed = discord.Embed(title="📋 群發紀錄", color=discord.Color.blue())
        for bc in entries[:10]:
            embed.add_field(
                name=f"#{bc.id} {bc.title}",
                value=(
                    f"{target_text(bc)} ・ {bc.created_dt:%Y-%m-%d %H:%M}\n"
                    f"已讀 {len(bc.read_ids)}/{len(bc.delivered)} ({bc.progress:.0%})"
                ),
                inline=False,
            )
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
        self.save()

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

        del self.broadcasts[id]
        self.save()
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
