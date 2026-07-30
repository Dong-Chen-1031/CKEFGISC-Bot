import logging

import discord
from discord import Interaction, app_commands
from discord.ext import commands, tasks

import settings
from utils import countdown as cd
from utils import ui
from utils.log import log


def preview_embed(entry: cd.Countdown, title: str, color=discord.Color.blue()) -> discord.Embed:
    """建立一個顯示倒數設定的嵌入"""
    embed = discord.Embed(title=title, color=color)
    embed.add_field(name="頻道", value=f"<#{entry.channel_id}>", inline=True)
    embed.add_field(name="名稱", value=entry.name, inline=True)
    embed.add_field(
        name="目標時間",
        value=f"{entry.target_dt:%Y-%m-%d %H:%M} ({settings.TIMEZONE})",
        inline=False,
    )
    embed.add_field(name="顯示為", value=f"`{entry.render()}`", inline=False)
    embed.set_footer(text=f"每 {settings.COUNTDOWN_UPDATE_INTERVAL} 分鐘自動更新一次")
    return embed


class CountdownCog(commands.Cog):
    countdown = app_commands.Group(
        name="countdown",
        description="用語音頻道名稱做日期倒數",
        guild_only=True,
        default_permissions=discord.Permissions(manage_channels=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.update_loop.start()

    async def cog_unload(self):
        self.update_loop.cancel()

    # ------------------------------------------------------------ 核心動作

    async def set_lock(self, channel: discord.VoiceChannel, lock: bool):
        """鎖住頻道：大家看得到但不能加入，純粹當顯示板用

        解鎖時是把權限設回「繼承」而不是明確允許，避免蓋掉伺服器原本的設定。
        """
        overwrite = channel.overwrites_for(channel.guild.default_role)
        overwrite.connect = False if lock else None
        overwrite.speak = False if lock else None
        await channel.set_permissions(
            channel.guild.default_role,
            overwrite=overwrite,
            reason="倒數頻道鎖定" if lock else "倒數頻道解鎖",
        )

    async def apply(self, entry: cd.Countdown) -> bool:
        """把倒數的結果套用到頻道名稱上，有實際改動才回傳 True

        名稱沒變就不送請求 — Discord 對頻道改名的限制是每 10 分鐘 2 次。
        """
        channel = self.bot.get_channel(entry.channel_id)
        if channel is None:
            return False

        new_name = entry.render()
        if channel.name == new_name:
            return False

        try:
            await channel.edit(name=new_name, reason="倒數更新")
        except discord.Forbidden:
            logging.warning(f"沒有權限修改倒數頻道 {entry.channel_id}")
            return False
        except discord.HTTPException as e:
            logging.warning(f"倒數頻道 {entry.channel_id} 更新失敗: {e}")
            return False
        return True

    @tasks.loop(minutes=settings.COUNTDOWN_UPDATE_INTERVAL)
    async def update_loop(self):
        updated = 0
        for entry in cd.all_countdowns():
            if await self.apply(entry):
                updated += 1
        if updated:
            logging.info(f"已更新 {updated} 個倒數頻道")

    @update_loop.before_loop
    async def before_update_loop(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        """頻道被刪掉就順手清掉設定"""
        if cd.delete_countdown(channel.id):
            logging.info(f"倒數頻道 {channel.id} 已被刪除，設定一併移除")

    # ------------------------------------------------------------ 指令

    @countdown.command(name="create", description="建立一個新的倒數語音頻道")
    @app_commands.describe(
        name="倒數的名稱，例如：畢業典禮",
        date="目標日期，例如 2025-01-01 或 1/1",
        time="目標時間，預設為當天 00:00，例如 08:30",
        category="要建立在哪個分類底下",
        template="頻道名稱模板，可用 /countdown help 查看變數",
        lock="是否鎖住頻道不讓人加入 (預設是)",
    )
    async def create(
        self,
        interaction: Interaction,
        name: str,
        date: str,
        time: str | None = None,
        category: discord.CategoryChannel | None = None,
        template: str | None = None,
        lock: bool = True,
    ):
        log(interaction, name=name, date=date, time=time)
        await interaction.response.defer(ephemeral=True)

        try:
            target = cd.parse_target(date, time)
            template = template or cd.DEFAULT_TEMPLATE
            cd.validate_template(template)
        except ValueError as e:
            await interaction.followup.send(embed=ui.info_embed(str(e), discord.Color.red()))
            return

        entry = cd.Countdown(
            guild_id=interaction.guild_id,
            channel_id=0,
            name=name,
            target=target.isoformat(),
            template=template,
        )

        try:
            channel = await interaction.guild.create_voice_channel(
                name=entry.render(),
                category=category,
                reason=f"{interaction.user} 建立倒數頻道",
            )
            await self.set_lock(channel, lock)
        except discord.Forbidden:
            await interaction.followup.send(
                embed=ui.info_embed("我沒有「管理頻道」的權限，無法建立頻道", discord.Color.red())
            )
            return

        entry.channel_id = channel.id
        cd.save_countdown(entry)

        await interaction.followup.send(embed=preview_embed(entry, "✅ 倒數頻道已建立"))

    @countdown.command(name="bind", description="把倒數綁到既有的語音頻道上")
    @app_commands.describe(
        channel="要拿來顯示倒數的語音頻道 (名稱會被覆蓋)",
        name="倒數的名稱，例如：畢業典禮",
        date="目標日期，例如 2025-01-01 或 1/1",
        time="目標時間，預設為當天 00:00，例如 08:30",
        template="頻道名稱模板，可用 /countdown help 查看變數",
        lock="是否鎖住頻道不讓人加入 (預設是)",
    )
    async def bind(
        self,
        interaction: Interaction,
        channel: discord.VoiceChannel,
        name: str,
        date: str,
        time: str | None = None,
        template: str | None = None,
        lock: bool = True,
    ):
        log(interaction, channel=channel.name, name=name, date=date)
        await interaction.response.defer(ephemeral=True)

        try:
            target = cd.parse_target(date, time)
            template = template or cd.DEFAULT_TEMPLATE
            cd.validate_template(template)
        except ValueError as e:
            await interaction.followup.send(embed=ui.info_embed(str(e), discord.Color.red()))
            return

        entry = cd.Countdown(
            guild_id=interaction.guild_id,
            channel_id=channel.id,
            name=name,
            target=target.isoformat(),
            template=template,
        )
        cd.save_countdown(entry)

        try:
            await self.set_lock(channel, lock)
        except discord.Forbidden:
            await interaction.followup.send(
                embed=ui.info_embed("我沒有權限修改這個頻道的權限設定", discord.Color.red())
            )
            return

        await self.apply(entry)
        await interaction.followup.send(embed=preview_embed(entry, "✅ 倒數已綁定"))

    @countdown.command(name="edit", description="修改既有倒數的設定")
    @app_commands.describe(
        channel="要修改的倒數頻道",
        name="新的倒數名稱",
        date="新的目標日期",
        time="新的目標時間",
        template="新的名稱模板",
        expired_template="倒數結束後要顯示的模板",
    )
    async def edit(
        self,
        interaction: Interaction,
        channel: discord.VoiceChannel,
        name: str | None = None,
        date: str | None = None,
        time: str | None = None,
        template: str | None = None,
        expired_template: str | None = None,
    ):
        log(interaction, channel=channel.name)
        await interaction.response.defer(ephemeral=True)

        entry = cd.get_countdown(channel.id)
        if entry is None:
            await interaction.followup.send(
                embed=ui.info_embed(f"{channel.mention} 還沒有設定倒數", discord.Color.red())
            )
            return

        try:
            if date or time:
                # 只給其中一個時，另一半沿用原本的設定
                base_date = date or f"{entry.target_dt:%Y-%m-%d}"
                base_time = time
                if base_time is None and " " not in base_date.strip():
                    # date 本身沒夾帶時間，才補上原本的時刻
                    base_time = f"{entry.target_dt:%H:%M}"
                entry.target = cd.parse_target(base_date, base_time).isoformat()
            if template:
                cd.validate_template(template)
                entry.template = template
            if expired_template:
                cd.validate_template(expired_template)
                entry.expired_template = expired_template
        except ValueError as e:
            await interaction.followup.send(embed=ui.info_embed(str(e), discord.Color.red()))
            return

        if name:
            entry.name = name
        entry = cd.save_countdown(entry)

        await self.apply(entry)
        await interaction.followup.send(embed=preview_embed(entry, "✅ 倒數已更新"))

    @countdown.command(name="list", description="列出這個伺服器所有的倒數")
    async def list_countdowns(self, interaction: Interaction):
        log(interaction)
        entries = cd.guild_countdowns(interaction.guild_id)
        if not entries:
            await interaction.response.send_message(
                embed=ui.info_embed("這個伺服器還沒有任何倒數"), ephemeral=True
            )
            return

        entries.sort(key=lambda c: c.target_dt)
        embed = discord.Embed(title="⏳ 倒數列表", color=discord.Color.blue())
        for entry in entries:
            status = "已結束" if entry.is_expired() else f"剩 {entry.fields()['days']} 天"
            embed.add_field(
                name=f"{entry.name} — {status}",
                value=(
                    f"<#{entry.channel_id}>\n"
                    f"目標：{entry.target_dt:%Y-%m-%d %H:%M}\n"
                    f"顯示：`{entry.render()}`"
                ),
                inline=False,
            )
        embed.set_footer(text=f"時區 {settings.TIMEZONE}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @countdown.command(name="remove", description="移除倒數設定")
    @app_commands.describe(channel="要移除的倒數頻道", delete_channel="是否連頻道一起刪掉")
    async def remove(
        self,
        interaction: Interaction,
        channel: discord.VoiceChannel,
        delete_channel: bool = False,
    ):
        log(interaction, channel=channel.name, delete_channel=delete_channel)
        await interaction.response.defer(ephemeral=True)

        if not cd.delete_countdown(channel.id):
            await interaction.followup.send(
                embed=ui.info_embed(f"{channel.mention} 沒有設定倒數", discord.Color.red())
            )
            return

        if delete_channel:
            try:
                await channel.delete(reason=f"{interaction.user} 移除倒數頻道")
            except discord.Forbidden:
                await interaction.followup.send(
                    embed=ui.info_embed("設定已移除，但我沒有權限刪除頻道", discord.Color.orange())
                )
                return
            await interaction.followup.send(embed=ui.info_embed("✅ 倒數與頻道都已刪除"))
            return

        await interaction.followup.send(
            embed=ui.info_embed(f"✅ 已移除 {channel.mention} 的倒數，頻道保留")
        )

    @countdown.command(name="refresh", description="立刻更新所有倒數頻道名稱")
    async def refresh(self, interaction: Interaction):
        log(interaction)
        await interaction.response.defer(ephemeral=True)

        entries = cd.guild_countdowns(interaction.guild_id)
        updated = 0
        for entry in entries:
            if await self.apply(entry):
                updated += 1

        await interaction.followup.send(
            embed=ui.info_embed(f"已檢查 {len(entries)} 個倒數，實際更新 {updated} 個")
        )

    @countdown.command(name="help", description="查看名稱模板可以用的變數")
    async def help_command(self, interaction: Interaction):
        log(interaction)
        embed = discord.Embed(
            title="📝 名稱模板說明",
            description=f"預設模板：`{cd.DEFAULT_TEMPLATE}`",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="可用變數",
            value="\n".join(f"`{k}` — {v}" for k, v in cd.PLACEHOLDERS.items()),
            inline=False,
        )
        embed.add_field(
            name="範例",
            value=(
                "`📅 {name} 還有 {days} 天`\n"
                "`{name} | {d}天{h}時`\n"
                "`⏳ {target_date} ({days}d)`"
            ),
            inline=False,
        )
        embed.set_footer(
            text=f"Discord 限制頻道每 10 分鐘只能改名 2 次，因此每 {settings.COUNTDOWN_UPDATE_INTERVAL} 分鐘才更新一次"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------ 錯誤處理

    async def cog_app_command_error(self, interaction: Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            message = "你需要「管理頻道」權限才能使用這個指令"
        elif isinstance(error, app_commands.BotMissingPermissions):
            message = "我缺少必要的權限，請確認我有「管理頻道」"
        else:
            logging.error(f"倒數指令發生錯誤: {error}", exc_info=error)
            message = f"發生未預期的錯誤：{error}"

        embed = ui.info_embed(message, discord.Color.red())
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(CountdownCog(bot))
    logging.info(f'{__name__} 已載入')
