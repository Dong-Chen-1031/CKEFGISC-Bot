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

@bot.event
async def on_ready():
    logging.info(f'已登入為 {bot.user.name}')
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