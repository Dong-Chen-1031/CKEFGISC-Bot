import asyncio
import logging

import discord
from discord import Interaction, app_commands
from discord.ext import commands

import settings
from utils import ui
from utils.log import log


def usage_text(guild: discord.Guild) -> str:
    """伺服器目前的表符使用量（靜態與動態各自計算額度）"""
    limit = guild.emoji_limit
    static = sum(1 for e in guild.emojis if not e.animated)
    animated = sum(1 for e in guild.emojis if e.animated)
    return f"靜態 {static}/{limit}・動態 {animated}/{limit}"


def can_manage_emojis(member: discord.Member | None) -> bool:
    """有沒有管理表情符號的權限（管理員一定有）"""
    if member is None:
        return False
    perms = member.guild_permissions
    return perms.manage_expressions or perms.administrator


class CopyPlan:
    """算好要複製哪些、哪些跳過、額度夠不夠"""

    def __init__(
        self,
        source: discord.Guild,
        target: discord.Guild,
        skip_existing: bool,
    ):
        self.source = source
        self.target = target

        existing = {e.name for e in target.emojis}
        limit = target.emoji_limit
        room = {
            False: limit - sum(1 for e in target.emojis if not e.animated),
            True: limit - sum(1 for e in target.emojis if e.animated),
        }

        self.to_copy: list[discord.Emoji] = []
        self.duplicated: list[discord.Emoji] = []
        self.no_room: list[discord.Emoji] = []

        for emoji in source.emojis:
            if skip_existing and emoji.name in existing:
                self.duplicated.append(emoji)
                continue
            if room[emoji.animated] <= 0:
                self.no_room.append(emoji)
                continue
            room[emoji.animated] -= 1
            self.to_copy.append(emoji)

    @property
    def is_empty(self) -> bool:
        return not self.to_copy

    def summary_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🎨 表情符號複製",
            description=f"**{self.source.name}** → **{self.target.name}**",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="✅ 將複製", value=f"{len(self.to_copy)} 個", inline=True
        )
        embed.add_field(
            name="⏭️ 同名跳過", value=f"{len(self.duplicated)} 個", inline=True
        )
        embed.add_field(
            name="🚫 額度不足", value=f"{len(self.no_room)} 個", inline=True
        )
        embed.add_field(
            name="目標伺服器目前用量", value=usage_text(self.target), inline=False
        )
        if self.to_copy:
            names = "、".join(f"`{e.name}`" for e in self.to_copy[:15])
            if len(self.to_copy) > 15:
                names += f" …等 {len(self.to_copy)} 個"
            embed.add_field(name="清單", value=names[:1024], inline=False)
        estimate = int(len(self.to_copy) * settings.EMOJI_COPY_DELAY)
        embed.set_footer(text=f"預計需要約 {estimate} 秒")
        return embed


class ConfirmView(discord.ui.View):
    """複製會直接改動伺服器，先讓使用者確認一次"""

    def __init__(self, cog: "EmojiCog", plan: CopyPlan, author_id: int):
        super().__init__(timeout=120)
        self.cog = cog
        self.plan = plan
        self.author_id = author_id

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                embed=ui.info_embed("這不是你的操作", discord.Color.red()),
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="開始複製", emoji="✅", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: Interaction, _button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()
        await self.cog.run_copy(interaction, self.plan)

    @discord.ui.button(label="取消", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: Interaction, _button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            embed=ui.info_embed("已取消，沒有複製任何表符"), view=self
        )
        self.stop()


class EmojiCog(commands.Cog):
    emoji = app_commands.Group(
        name="emoji",
        description="跨伺服器複製表情符號",
        guild_only=True,
        default_permissions=discord.Permissions(manage_expressions=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def interaction_check(self, interaction: Interaction) -> bool:
        """要在「目前這個」伺服器有管理表符的權限"""
        if can_manage_emojis(interaction.user):
            return True
        raise app_commands.MissingPermissions(["manage_expressions"])

    def shared_guilds(self, user_id: int) -> list[discord.Guild]:
        """機器人與這位使用者都在、且對方有管理表符權限的伺服器"""
        result = []
        for guild in self.bot.guilds:
            member = guild.get_member(user_id)
            if member and can_manage_emojis(member):
                result.append(guild)
        return result

    async def source_autocomplete(
        self, interaction: Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        current = current.lower()
        choices = []
        for guild in self.shared_guilds(interaction.user.id):
            if guild.id == interaction.guild_id:
                continue  # 不能複製自己
            if current and current not in guild.name.lower():
                continue
            choices.append(
                app_commands.Choice(
                    name=f"{guild.name} ({len(guild.emojis)} 個表符)"[:100],
                    value=str(guild.id),
                )
            )
        return choices[:25]

    # ------------------------------------------------------------ 執行

    async def run_copy(self, interaction: Interaction, plan: CopyPlan):
        created, failed = [], []

        for emoji in plan.to_copy:
            try:
                image = await emoji.read()
                new = await plan.target.create_custom_emoji(
                    name=emoji.name,
                    image=image,
                    reason=f"{interaction.user} 從 {plan.source.name} 複製",
                )
                created.append(new)
            except discord.HTTPException as e:
                failed.append((emoji, e))
                logging.warning(f"複製表符 {emoji.name} 失敗: {e}")
            # 建立表符的速率限制很緊，每個之間留間隔
            await asyncio.sleep(settings.EMOJI_COPY_DELAY)

        log(
            interaction,
            source=plan.source.name,
            created=len(created),
            failed=len(failed),
        )

        embed = discord.Embed(
            title="🎨 複製完成",
            description=f"**{plan.source.name}** → **{plan.target.name}**",
            color=discord.Color.green() if not failed else discord.Color.orange(),
        )
        embed.add_field(name="✅ 成功", value=f"{len(created)} 個", inline=True)
        embed.add_field(name="⏭️ 跳過", value=f"{len(plan.duplicated)} 個", inline=True)
        embed.add_field(name="❌ 失敗", value=f"{len(failed)} 個", inline=True)
        if created:
            shown = " ".join(str(e) for e in created[:20])
            embed.add_field(name="新增的表符", value=shown[:1024], inline=False)
        if failed:
            detail = "\n".join(f"`{e.name}` — {err}" for e, err in failed[:5])
            embed.add_field(name="失敗原因", value=detail[:1024], inline=False)
        embed.add_field(
            name="目標伺服器用量", value=usage_text(plan.target), inline=False
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------ 指令

    @emoji.command(name="copy", description="把另一個伺服器的表情符號複製到這裡")
    @app_commands.describe(
        source="來源伺服器（你必須在該伺服器也有管理表情符號的權限）",
        skip_existing="跳過這裡已經有同名的表符 (預設是)",
    )
    @app_commands.autocomplete(source=source_autocomplete)
    async def copy(
        self,
        interaction: Interaction,
        source: str,
        skip_existing: bool = True,
    ):
        log(interaction, source=source)
        await interaction.response.defer(ephemeral=True)

        if not source.isdigit():
            await interaction.followup.send(
                embed=ui.info_embed(
                    "請從選單裡挑一個伺服器，或直接填伺服器 ID", discord.Color.red()
                )
            )
            return

        source_guild = self.bot.get_guild(int(source))
        if source_guild is None:
            await interaction.followup.send(
                embed=ui.info_embed(
                    "找不到那個伺服器，機器人可能不在裡面", discord.Color.red()
                )
            )
            return

        if source_guild.id == interaction.guild_id:
            await interaction.followup.send(
                embed=ui.info_embed("來源和目標是同一個伺服器", discord.Color.red())
            )
            return

        # 關鍵檢查：使用者在來源伺服器也要有權限，否則等於可以偷走
        # 機器人所在的任何伺服器的表符
        if not can_manage_emojis(source_guild.get_member(interaction.user.id)):
            await interaction.followup.send(
                embed=ui.info_embed(
                    f"你在 **{source_guild.name}** 沒有管理表情符號的權限，"
                    "不能從那裡複製",
                    discord.Color.red(),
                )
            )
            return

        if not interaction.guild.me.guild_permissions.manage_expressions:
            await interaction.followup.send(
                embed=ui.info_embed(
                    "我在這個伺服器沒有「管理表情符號」權限", discord.Color.red()
                )
            )
            return

        if not source_guild.emojis:
            await interaction.followup.send(
                embed=ui.info_embed(
                    f"**{source_guild.name}** 沒有任何表情符號", discord.Color.orange()
                )
            )
            return

        plan = CopyPlan(source_guild, interaction.guild, skip_existing)
        if plan.is_empty:
            reason = (
                "全部都已經有同名的表符了"
                if plan.duplicated and not plan.no_room
                else "目標伺服器的表符額度已滿"
            )
            await interaction.followup.send(
                embed=ui.info_embed(f"沒有可以複製的表符 — {reason}", discord.Color.orange())
            )
            return

        await interaction.followup.send(
            embed=plan.summary_embed(),
            view=ConfirmView(self, plan, interaction.user.id),
        )

    @emoji.command(name="list", description="看看機器人在哪些伺服器、各有幾個表符")
    async def list_guilds(self, interaction: Interaction):
        log(interaction)
        guilds = self.shared_guilds(interaction.user.id)

        embed = discord.Embed(
            title="🎨 可以當來源的伺服器",
            description="機器人與你都在、且你有管理表情符號權限的伺服器",
            color=discord.Color.blurple(),
        )
        for guild in guilds[:20]:
            here = "（目前所在）" if guild.id == interaction.guild_id else ""
            embed.add_field(
                name=f"{guild.name}{here}",
                value=f"{len(guild.emojis)} 個表符・{usage_text(guild)}",
                inline=False,
            )
        if not guilds:
            embed.description = "找不到符合條件的伺服器"
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------ 錯誤處理

    async def cog_app_command_error(
        self, interaction: Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, app_commands.MissingPermissions):
            message = "你需要「管理表情符號」權限才能使用這個指令"
        else:
            logging.error(f"表符指令發生錯誤: {error}", exc_info=error)
            message = f"發生未預期的錯誤：{error}"

        embed = ui.info_embed(message, discord.Color.red())
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(EmojiCog(bot))
    logging.info(f'{__name__} 已載入')
