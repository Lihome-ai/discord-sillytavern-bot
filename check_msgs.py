import asyncio
import discord
from dotenv import load_dotenv
import os
load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

async def main():
    await client.login(os.getenv('DISCORD_TOKEN'))
    thread_id = 1566766421826871326  # 锁测试
    thread = client.get_channel(thread_id)
    with open('result.txt', 'w', encoding='utf-8') as f:
        if thread:
            msgs = [m async for m in thread.history(limit=10)]
            f.write(f'消息数：{len(msgs)}\n')
            for i, m in enumerate(msgs):
                f.write(f'  {i}: {m.author} - embeds: {len(m.embeds)}\n')
        else:
            f.write('没找到频道\n')
    await client.close()

asyncio.run(main())
