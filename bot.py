from rich.traceback import install
# 安裝Rich traceback
install(show_locals=True)

import sys
import discord
from discord.ext import commands
import os
import settings
import logging
import asyncio
from rich import print
from discord import Embed

from utils import log
from utils.db import init_db

intents = discord.Intents.all()

bot = commands.Bot(command_prefix=settings.PREFIX, intents=intents)

# 邀請連結要求的權限，對應各個 Cog 實際會用到的功能
INVITE_PERMISSIONS = discord.Permissions(
    view_channel=True,        # 看得到頻道
    send_messages=True,       # 訊息紀錄送通知
    embed_links=True,         # 通知都是 Embed
    manage_channels=True,     # 倒數：建立、改名、刪除頻道
    manage_roles=True,        # 倒數：設定頻道權限覆寫（鎖頻道）
    connect=True,             # 倒數：鎖頻道時要能覆寫「連線」
    speak=True,               # 倒數：鎖頻道時要能覆寫「說話」
    manage_expressions=True,  # 複製表情符號
    view_audit_log=True,      # 訊息紀錄：從稽核紀錄查刪除者
)

@bot.event
async def on_ready():
    logging.info(f'已登入為 {bot.user.name}')
    invite = discord.utils.oauth_url(
        bot.user.id,
        permissions=INVITE_PERMISSIONS,
        scopes=('bot', 'applications.commands'),
    )
    logging.info(f'邀請連結: {invite}')
    try:
        synced = await bot.tree.sync()
        logging.info(f'已同步 {len(synced)} 個斜線指令')
    except Exception as e:
        logging.error(f'同步指令失敗: {e}')


# 異步函數來載入模組
async def load_extensions_all():
    logging.info('正在載入模組...')
    for filename in os.listdir('./cogs'):
        if filename.endswith('.py'):
            await bot.load_extension(f'cogs.{filename[:-3]}')
            # logging.info(f'已載入模組: {filename[:-3]}')

async def main():
    # 建表 + 需要時把舊的 JSON 資料搬進資料庫，要在載入 Cogs 之前完成
    init_db()
    async with bot:
        await load_extensions_all()
        await bot.start(settings.DISCORD_BOT_TOKEN)

if __name__ == '__main__':
    logging.info('啟動機器人中...')
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info('機器人已停止 (KeyboardInterrupt)')