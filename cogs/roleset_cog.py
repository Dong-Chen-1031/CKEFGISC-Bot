"""身分組集合

用集合運算描述一群成員，例如 `(@幹部 | @顧問) - @畢業生`，然後：
- 衍伸身分組：把某個身分組綁上運算式，機器人自動增減成員讓它等於運算結果
- 提及集合：一次 @ 運算結果裡的所有成員

衍伸身分組靠 on_member_update 即時同步，另外定期整個伺服器校正一次，
補上事件漏掉或機器人離線期間的變動。運算式語法見 utils/roleset.py。
"""

import asyncio
import logging
from collections import defaultdict

import discord
from discord import Interaction, app_commands
from discord.ext import commands, tasks

import settings
from utils import roleset as rs
from utils import ui
from utils.log import log

SYNC_REASON = "衍伸身分組自動同步"
# 預覽與清單裡最多列出幾位成員
PREVIEW_MEMBERS = 40
FIELD_LIMIT = 1024
# 自動補全最多算幾次「成員 × 選項」的人數，超過就不附人數
AUTOCOMPLETE_BUDGET = 200_000
# 運算式寫錯時附在錯誤訊息後面
HELP_HINT = "語法可以用 /roleset help 查看"


def trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def role_problem(role: discord.Role, user: discord.Member) -> str | None:
    """這個身分組能不能拿來當衍伸身分組，不行的話回傳原因"""
    guild = role.guild
    if role.is_default():
        return "不能把 @everyone 設成衍伸身分組"
    if role.managed:
        return f"{role.mention} 由整合（機器人、加成等）管理，無法手動指派"
    me = guild.me
    if not me.guild_permissions.manage_roles:
        return "我沒有「管理身分組」權限"
    if role >= me.top_role:
        return f"{role.mention} 不低於我的最高身分組，請把我的身分組拉到它上面"
    if user.id != guild.owner_id and role >= user.top_role:
        return f"{role.mention} 不低於你的最高身分組，你不能管理它"
    return None


def plain_expression(node: rs.Node, guild: discord.Guild) -> str:
    """運算式寫成純文字，身分組與成員只顯示名稱、不用提及格式

    給一般訊息用：訊息裡的 <@成員> 一定會顯示成 @ 的樣子，
    `@執秘 - @某人` 裡被排除的人就算沒收到通知，看起來也像被 @ 了。
    """

    def label(is_role: bool, target_id: int) -> str:
        if is_role:
            found = guild.get_role(target_id)
            name = found.name if found else "已刪除的身分組"
        else:
            found = guild.get_member(target_id)
            name = found.display_name if found else "不在伺服器的成員"
        return "@" + discord.utils.escape_markdown(name)

    return rs.display(node.canonical(), label)


def member_list(members: list[discord.Member], limit: int = PREVIEW_MEMBERS) -> str:
    text = " ".join(m.mention for m in members[:limit])
    if len(members) > limit:
        text += f" …等 {len(members)} 人"
    return trim(text, FIELD_LIMIT) or "*（沒有人）*"


class ConfirmView(discord.ui.View):
    """會動到很多人的操作先讓使用者確認一次，確認後呼叫 on_confirm(interaction)"""

    def __init__(self, author_id: int, on_confirm, label: str):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.on_confirm = on_confirm
        self.confirm.label = label

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                embed=ui.info_embed("這不是你的操作", discord.Color.red()),
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="確認", emoji="✅", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: Interaction, _button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()
        await self.on_confirm(interaction)

    @discord.ui.button(label="取消", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: Interaction, _button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=ui.info_embed("已取消"), view=self)
        self.stop()


class RoleSetCog(commands.Cog):
    """衍伸身分組與集合提及"""

    # 各子指令需要的權限不同（preview 所有人都能用），所以群組不掛 default_permissions，
    # 改在每個指令上各自檢查
    roleset = app_commands.Group(
        name="roleset",
        description="身分組集合：衍伸身分組與集合提及",
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # 事件同步與整批校正不能同時改同一個伺服器
        self.locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        # guild_id -> 排序好的規則；設定一改就清掉
        self.rule_cache: dict[int, list[rs.Rule]] = {}

    async def cog_load(self):
        self.sync_loop.start()

    async def cog_unload(self):
        self.sync_loop.cancel()

    # ------------------------------------------------------------ 規則快取

    def rules(self, guild_id: int) -> list[rs.Rule]:
        if guild_id not in self.rule_cache:
            self.rule_cache[guild_id] = rs.load_rules(guild_id)
        return self.rule_cache[guild_id]

    def watched_roles(self, guild_id: int) -> set[int]:
        """變動時需要重算的身分組：被引用的，加上衍伸身分組本身（被手動改了要改回來）"""
        watched = set()
        for role_id, node in self.rules(guild_id):
            watched.add(role_id)
            watched |= node.roles()
        return watched

    def invalidate(self, guild_id: int):
        self.rule_cache.pop(guild_id, None)

    # ------------------------------------------------------------ 同步

    async def apply_member(self, member: discord.Member, rules: list[rs.Rule]) -> bool:
        """把一位成員的衍伸身分組改成應有的樣子，有改動就回傳 True

        呼叫端要先拿到伺服器的鎖。權限不足等錯誤會往上丟。
        """
        if not rules:
            return False

        current = rs.member_roles(member)
        target = rs.apply_rules(current, rules)
        guild = member.guild
        to_add = [r for i in target - current if (r := guild.get_role(i))]
        to_remove = [r for i in current - target if (r := guild.get_role(i))]
        if not to_add and not to_remove:
            return False

        if to_add:
            await member.add_roles(*to_add, reason=SYNC_REASON)
        if to_remove:
            await member.remove_roles(*to_remove, reason=SYNC_REASON)
        return True

    async def sync_member(self, guild: discord.Guild, member_id: int):
        """事件觸發的單人同步，錯誤只記 log"""
        async with self.locks[guild.id]:
            # 等鎖的期間成員可能又變了，重新拿一次最新的
            member = guild.get_member(member_id)
            if member is None:
                return
            try:
                await self.apply_member(member, self.rules(guild.id))
            except discord.HTTPException as e:
                logging.warning(f"衍伸身分組：同步 {member} ({guild.name}) 失敗: {e}")

    async def sync_guild(self, guild: discord.Guild) -> tuple[int, int]:
        """整個伺服器校正一次，回傳 (改動人數, 失敗人數)"""
        changed = failed = 0
        async with self.locks[guild.id]:
            rules = self.rules(guild.id)
            if not rules:
                return 0, 0
            if not guild.chunked:
                await guild.chunk()
            for member in list(guild.members):
                try:
                    if await self.apply_member(member, rules):
                        changed += 1
                        await asyncio.sleep(settings.ROLESET_EDIT_DELAY)
                except discord.HTTPException as e:
                    failed += 1
                    logging.warning(f"衍伸身分組：同步 {member} ({guild.name}) 失敗: {e}")
        if changed or failed:
            logging.info(f"衍伸身分組：{guild.name} 校正完成，改動 {changed} 人、失敗 {failed} 人")
        return changed, failed

    @tasks.loop(minutes=settings.ROLESET_SYNC_INTERVAL)
    async def sync_loop(self):
        # 第一次執行就在剛開機時，順便補上離線期間的變動
        for guild_id in rs.all_guild_ids():
            guild = self.bot.get_guild(guild_id)
            if guild is not None:
                await self.sync_guild(guild)

    @sync_loop.before_loop
    async def before_sync_loop(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------ 事件

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.roles == after.roles:
            return
        changed = {r.id for r in before.roles} ^ {r.id for r in after.roles}
        if changed & self.watched_roles(after.guild.id):
            await self.sync_member(after.guild, after.id)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        # 補集（例如 ~@已驗證）會包含剛加入的人
        if self.rules(member.guild.id):
            await self.sync_member(member.guild, member.id)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        guild_id = role.guild.id
        if rs.delete_derived(role.id):
            logging.info(f"衍伸身分組：{role.name} 被刪除，已移除設定")

        for row in rs.guild_derived(guild_id):
            if row.enabled and role.id in row.node.roles():
                row.enabled = False
                rs.save_derived(row)
                logging.warning(
                    f"衍伸身分組：<@&{row.role_id}> 引用的身分組 {role.name} 被刪除，已停用"
                )
        self.invalidate(guild_id)

    # ------------------------------------------------------------ 指令用小工具

    async def error(self, interaction: Interaction, message: str):
        await interaction.followup.send(
            embed=ui.info_embed(message, discord.Color.red()), ephemeral=True
        )

    async def expression_autocomplete(
        self, interaction: Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """依游標位置建議下一步，並在選項後面附上套用後的人數（規則見 rs.Completer）"""
        guild = interaction.guild
        if guild is None:
            return []

        completer = rs.Completer(guild)
        values = completer.suggest(current)
        # 每個選項都要把全部成員算一次，人多的伺服器只算前面幾個，免得超過 3 秒的回應期限
        budget = AUTOCOMPLETE_BUDGET
        member_sets = None
        choices = []
        for value in values:
            name = value
            if budget >= len(guild.members):
                node = completer.node(value)
                if node is not None:
                    if member_sets is None:
                        member_sets = [rs.member_roles(m) for m in guild.members]
                    budget -= len(member_sets)
                    name = rs.with_hint(value, sum(1 for ids in member_sets if node.matches(ids)))
            choices.append(app_commands.Choice(name=name, value=value))
        return choices

        # 有打字才列成員，不然清單會被一堆不相干的人塞滿
        if not partial:
            return choices
        role_names = {r.name.lower() for r in guild.roles}
        for member in guild.members:
            names = [n for n in (member.nick, member.global_name, member.name) if n]
            if not any(partial in n.lower() for n in names):
                continue
            # 名稱跟身分組撞名、或有人同名時，解析會選到別的，改用 ID
            name = member.display_name
            clash = name.lower() in role_names or sum(
                1 for m in guild.members if name.lower() in {(n or "").lower() for n in (m.name, m.global_name, m.nick)}
            ) > 1
            value = prefix + (str(member.id) if clash else rs.name_token(name))
            if len(value) > 100:
                continue
            choices.append(app_commands.Choice(name=value, value=value))
            if len(choices) >= 25:
                break
        return choices

    def preview_changes(
        self, guild: discord.Guild, role_id: int, node: rs.Node
    ) -> dict[int, tuple[int, int]]:
        """套上新規則後，各衍伸身分組會 (加入, 移除) 幾個人"""
        rules = [(rid, n) for rid, n in self.rules(guild.id) if rid != role_id]
        rules = rs.order_rules(rules + [(role_id, node)])

        diff: dict[int, list[int]] = defaultdict(lambda: [0, 0])
        for member in guild.members:
            current = rs.member_roles(member)
            target = rs.apply_rules(current, rules)
            for rid in target - current:
                diff[rid][0] += 1
            for rid in current - target:
                diff[rid][1] += 1
        return {rid: (a, r) for rid, (a, r) in diff.items()}

    # ------------------------------------------------------------ 衍伸身分組

    @roleset.command(name="derive", description="把現有身分組設成衍伸身分組，成員會自動跟著運算式變動")
    @app_commands.describe(
        role="要自動維護的身分組（原有成員會被調整成運算結果）",
        expression="集合運算式，例如 (@幹部 | @顧問) - @畢業生",
    )
    @app_commands.checks.has_permissions(manage_roles=True)
    async def derive(self, interaction: Interaction, role: discord.Role, expression: str):
        log(interaction, role=role.name, expression=expression)
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        problem = role_problem(role, interaction.user)
        if problem:
            await self.error(interaction, problem)
            return
        try:
            node = rs.parse(expression, guild)
        except rs.ExprError as e:
            await self.error(interaction, f"運算式有誤：{e}\n{HELP_HINT}")
            return
        cycle = rs.find_cycle(role.id, node, rs.dependency_graph(guild.id, exclude=role.id))
        if cycle:
            path = " → ".join(f"<@&{i}>" for i in cycle)
            await self.error(interaction, f"這樣會形成循環：{path}")
            return

        if not guild.chunked:
            await guild.chunk()
        changes = self.preview_changes(guild, role.id, node)
        added, removed = changes.get(role.id, (0, 0))

        old = rs.get_derived(role.id)
        embed = discord.Embed(
            title="🧮 設定衍伸身分組",
            description=f"{role.mention} = {node.display()}",
            color=discord.Color.blurple(),
        )
        if old is not None:
            embed.add_field(name="原本的運算式", value=trim(rs.display(old.expression), FIELD_LIMIT), inline=False)
        embed.add_field(name="➕ 會加入", value=f"{added} 人", inline=True)
        embed.add_field(name="➖ 會移除", value=f"{removed} 人", inline=True)
        others = [
            f"<@&{rid}> +{a} / -{r}" for rid, (a, r) in changes.items() if rid != role.id
        ]
        if others:
            embed.add_field(name="連動的其他衍伸身分組", value=trim("\n".join(others), FIELD_LIMIT), inline=False)
        embed.set_footer(text="確認後會立刻同步，之後成員變動時也會自動調整")

        async def on_confirm(button_interaction: Interaction):
            # 等按鈕的期間別人可能也改了設定，存檔前再擋一次循環
            if rs.find_cycle(role.id, node, rs.dependency_graph(guild.id, exclude=role.id)):
                await button_interaction.followup.send(
                    embed=ui.info_embed("設定在這段期間被改過，現在會形成循環，請重新操作", discord.Color.red()),
                    ephemeral=True,
                )
                return
            rs.save_derived(
                rs.DerivedRole(
                    role_id=role.id,
                    guild_id=guild.id,
                    expression=node.canonical(),
                    created_by=interaction.user.id,
                )
            )
            self.invalidate(guild.id)
            await self.report_sync(button_interaction, guild, f"✅ 已將 {role.mention} 設成衍伸身分組")

        await interaction.followup.send(
            embed=embed, view=ConfirmView(interaction.user.id, on_confirm, "套用"), ephemeral=True
        )

    @roleset.command(name="create", description="建立一個新的衍伸身分組")
    @app_commands.describe(
        name="新身分組的名稱",
        expression="集合運算式，例如 @高一 & ~@已繳費",
    )
    @app_commands.checks.has_permissions(manage_roles=True)
    async def create(
        self,
        interaction: Interaction,
        name: app_commands.Range[str, 1, 100],
        expression: str,
    ):
        log(interaction, name=name, expression=expression)
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        if not guild.me.guild_permissions.manage_roles:
            await self.error(interaction, "我沒有「管理身分組」權限")
            return
        try:
            node = rs.parse(expression, guild)
        except rs.ExprError as e:
            await self.error(interaction, f"運算式有誤：{e}\n{HELP_HINT}")
            return

        try:
            role = await guild.create_role(
                name=name, reason=f"{interaction.user} 建立衍伸身分組：{node.canonical()}"
            )
        except discord.HTTPException as e:
            await self.error(interaction, f"建立身分組失敗：{e}")
            return

        # 新身分組還沒被任何人引用，不可能形成循環
        rs.save_derived(
            rs.DerivedRole(
                role_id=role.id,
                guild_id=guild.id,
                expression=node.canonical(),
                created_by=interaction.user.id,
            )
        )
        self.invalidate(guild.id)
        await self.report_sync(interaction, guild, f"✅ 已建立 {role.mention} = {node.display()}")

    async def report_sync(self, interaction: Interaction, guild: discord.Guild, title: str):
        """立刻校正一次並回報結果"""
        changed, failed = await self.sync_guild(guild)
        text = f"{title}\n已同步 {changed} 位成員"
        if failed:
            text += f"，{failed} 位失敗（請確認我的身分組位置夠高）"
        color = discord.Color.green() if not failed else discord.Color.orange()
        await interaction.followup.send(embed=ui.info_embed(text, color), ephemeral=True)

    @roleset.command(name="remove", description="停止自動維護某個衍伸身分組（身分組與成員保留）")
    @app_commands.describe(role="要停止維護的衍伸身分組")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def remove(self, interaction: Interaction, role: discord.Role):
        log(interaction, role=role.name)
        if not rs.delete_derived(role.id):
            await interaction.response.send_message(
                embed=ui.info_embed(f"{role.mention} 不是衍伸身分組", discord.Color.orange()),
                ephemeral=True,
            )
            return
        self.invalidate(interaction.guild_id)
        await interaction.response.send_message(
            embed=ui.info_embed(f"✅ {role.mention} 已不再自動維護，現有成員保持不變"),
            ephemeral=True,
        )

    @roleset.command(name="list", description="列出這個伺服器的衍伸身分組")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def list_derived(self, interaction: Interaction):
        log(interaction)
        guild = interaction.guild
        rows = rs.guild_derived(guild.id)
        if not rows:
            await interaction.response.send_message(
                embed=ui.info_embed("這個伺服器還沒有衍伸身分組"), ephemeral=True
            )
            return

        embed = discord.Embed(
            title="🧮 衍伸身分組",
            description=f"共 {len(rows)} 個，每 {settings.ROLESET_SYNC_INTERVAL} 分鐘完整校正一次",
            color=discord.Color.blurple(),
        )
        for row in rows[:25]:
            role = guild.get_role(row.role_id)
            count = f"{len(role.members)} 人" if role else "身分組已不存在"
            state = "✅" if row.enabled else "⚠️ 已停用（引用的身分組被刪除，請用 derive 重新設定）"
            embed.add_field(
                name=trim(f"{role.name if role else row.role_id}・{count}", 256),
                value=trim(f"<@&{row.role_id}> = {rs.display(row.expression)}\n{state}", FIELD_LIMIT),
                inline=False,
            )
        if len(rows) > 25:
            embed.set_footer(text=f"還有 {len(rows) - 25} 個沒顯示")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @roleset.command(name="sync", description="立刻把所有衍伸身分組完整校正一次")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def sync(self, interaction: Interaction):
        log(interaction)
        await interaction.response.defer(ephemeral=True)
        if not self.rules(interaction.guild_id):
            await self.error(interaction, "這個伺服器沒有啟用中的衍伸身分組")
            return
        await self.report_sync(interaction, interaction.guild, "✅ 校正完成")

    # ------------------------------------------------------------ 說明

    @roleset.command(name="help", description="查看集合運算式的語法與指令說明")
    async def help_command(self, interaction: Interaction):
        log(interaction)
        embed = discord.Embed(
            title="🧮 身分組集合說明",
            description=(
                "把身分組當成「成員的集合」來運算，例如：\n"
                "`(@幹部 | @顧問) - @畢業生` → 幹部或顧問，但不含畢業生"
            ),
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="運算子",
            value=(
                "`A | B`　`A + B`　`A ∪ B` — 聯集：有 A 或有 B\n"
                "`A & B`　`A ∩ B` — 交集：同時有 A 和 B\n"
                "`A - B`　`A ∖ B` — 差集：有 A 但沒有 B\n"
                "`A ^ B`　`A △ B` — 對稱差：A、B 只有其中一個\n"
                "`~A`　`!A`　`¬A` — 補集：沒有 A 的人\n"
                "`*`　`@everyone` — 全集：所有成員（包含機器人）\n"
                "`bot`　`機器人` — 所有機器人\n"
                "`human`　`真人` — 所有真人\n"
                "`( … )` — 括號，先算裡面"
            ),
            inline=False,
        )
        embed.add_field(
            name="優先序",
            value="補集 ＞ 交集 ＞ 聯集／差集／對稱差（同級由左到右算），不確定就加括號",
            inline=False,
        )
        embed.add_field(
            name="身分組與個人的寫法",
            value=(
                "・直接 @ 身分組或成員（成員就是只有一個人的集合）\n"
                "・名稱：`幹部` 或 `@幹部`；找不到身分組才會找同名的成員\n"
                "・名稱有空白或符號時加引號：`\"高三 甲\"`、`「a-b」`\n"
                "・身分組或成員的 ID：`123456789`"
            ),
            inline=False,
        )
        embed.add_field(
            name="自動補全",
            value=(
                "輸入框會依目前打到哪裡建議身分組、成員或運算子，"
                "選項後面的「→ N 人」是選了之後的人數"
            ),
            inline=False,
        )
        embed.add_field(
            name="範例",
            value=(
                "`~@已驗證 - bot` — 還沒驗證的真人\n"
                "`bot & @音樂` — 有音樂身分組的機器人\n"
                "`@高三 - @小華` — 高三除了小華以外的人\n"
                "`@高二 & @資訊組` — 高二的資訊組成員\n"
                "`@高一 - @已繳費` — 還沒繳費的高一\n"
                "`(@幹部 | @顧問) - @畢業生` — 現任幹部與顧問"
            ),
            inline=False,
        )
        embed.add_field(
            name="指令",
            value=(
                "`/roleset preview` — 看運算式包含哪些人（所有人可用）\n"
                "`/roleset mention` — @ 運算結果的所有人（需「提及所有人」權限）\n"
                "`/roleset derive`／`create` — 設定／建立衍伸身分組\n"
                "`/roleset list`／`sync`／`remove` — 查看／校正／停止衍伸身分組\n"
                "*衍伸身分組相關指令需要「管理身分組」權限*"
            ),
            inline=False,
        )
        embed.set_footer(
            text=(
                "全集包含機器人，要排除就加上 - bot。名稱剛好叫 bot、human 的身分組請加引號。衍伸身分組的成員完全由運算式決定，手動增減會被改回來；"
                f"每 {settings.ROLESET_SYNC_INTERVAL} 分鐘完整校正一次"
            )
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------ 集合查詢與提及

    @roleset.command(name="preview", description="看看一個運算式包含哪些成員（不會 @ 任何人）")
    @app_commands.describe(expression="集合運算式，例如 @高二 & @資訊組")
    async def preview(self, interaction: Interaction, expression: str):
        log(interaction, expression=expression)
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        try:
            node = rs.parse(expression, guild)
        except rs.ExprError as e:
            await self.error(interaction, f"運算式有誤：{e}\n{HELP_HINT}")
            return

        if not guild.chunked:
            await guild.chunk()
        members = rs.evaluate(node, guild)
        embed = discord.Embed(
            title=f"🔍 共 {len(members)} 人",
            description=trim(node.display(), 4096),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="成員", value=member_list(members), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @roleset.command(name="mention", description="在這個頻道 @ 運算式算出來的所有成員")
    @app_commands.describe(
        expression="集合運算式，例如 (@幹部 | @顧問) - @畢業生",
        message="要一起附上的訊息（選填）",
    )
    @app_commands.checks.has_permissions(mention_everyone=True)
    async def mention(
        self,
        interaction: Interaction,
        expression: str,
        message: app_commands.Range[str, 1, 1500] | None = None,
    ):
        log(interaction, expression=expression)
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        try:
            node = rs.parse(expression, guild)
        except rs.ExprError as e:
            await self.error(interaction, f"運算式有誤：{e}\n{HELP_HINT}")
            return

        if not guild.chunked:
            await guild.chunk()
        members = rs.evaluate(node, guild)
        if not members:
            await self.error(interaction, "這個運算式沒有包含任何人，沒有送出訊息")
            return
        if len(members) > settings.ROLESET_MENTION_MAX:
            await self.error(
                interaction,
                f"這個運算式包含 {len(members)} 人，超過上限 {settings.ROLESET_MENTION_MAX} 人",
            )
            return

        async def send(button_interaction: Interaction):
            await self.send_mentions(button_interaction, node, members, message)

        if len(members) <= settings.ROLESET_MENTION_CONFIRM:
            await send(interaction)
            return

        embed = discord.Embed(
            title=f"📢 確定要提及 {len(members)} 人嗎？",
            description=trim(node.display(), 4096),
            color=discord.Color.orange(),
        )
        embed.add_field(name="成員", value=member_list(members), inline=False)
        await interaction.followup.send(
            embed=embed, view=ConfirmView(interaction.user.id, send, "送出"), ephemeral=True
        )

    async def send_mentions(
        self,
        interaction: Interaction,
        node: rs.Node,
        members: list[discord.Member],
        message: str | None,
    ):
        header = f"📢 **{discord.utils.escape_markdown(interaction.user.display_name)}** 提及了 {plain_expression(node, interaction.guild)}（{len(members)} 人）"
        if message:
            header += f"\n{message}"
        chunks = rs.chunk_mentions(header, [m.id for m in members])

        # 只放行這則逐一列出的使用者；運算式裡的身分組與個人提及只是顯示用，不能真的 ping 到
        try:
            for chunk, ids in chunks:
                allowed = discord.AllowedMentions(
                    everyone=False, roles=False, users=[discord.Object(i) for i in ids]
                )
                await interaction.channel.send(chunk, allowed_mentions=allowed)
        except discord.Forbidden:
            await self.error(interaction, "我在這個頻道沒有發送訊息的權限")
            return
        except discord.HTTPException as e:
            await self.error(interaction, f"訊息送出失敗：{e}")
            return

        await interaction.followup.send(
            embed=ui.info_embed(f"✅ 已提及 {len(members)} 人（{len(chunks)} 則訊息）"),
            ephemeral=True,
        )

    # 自動補全綁定（放在指令都定義完之後）
    derive.autocomplete("expression")(expression_autocomplete)
    create.autocomplete("expression")(expression_autocomplete)
    preview.autocomplete("expression")(expression_autocomplete)
    mention.autocomplete("expression")(expression_autocomplete)

    # ------------------------------------------------------------ 錯誤處理

    async def cog_app_command_error(
        self, interaction: Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, app_commands.MissingPermissions):
            names = {
                "manage_roles": "管理身分組",
                "mention_everyone": "提及 @everyone、@here 和所有身分組",
            }
            missing = "、".join(names.get(p, p) for p in error.missing_permissions)
            message = f"你需要「{missing}」權限才能使用這個指令"
        else:
            logging.error(f"身分組集合指令發生錯誤: {error}", exc_info=error)
            message = f"發生未預期的錯誤：{error}"

        embed = ui.info_embed(message, discord.Color.red())
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(RoleSetCog(bot))
    logging.info(f'{__name__} 已載入')
