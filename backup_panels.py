"""补面板脚本：给所有旧帖子补资源面板（独立运行）"""
import asyncio
import discord
from discord import ui
from config import DISCORD_TOKEN
from database import init_db, has_panel, set_panel

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
client = discord.Client(intents=intents)

# 直接定义ResourcePanelView（复制自main.py）
class ResourcePanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

@client.event
async def on_ready():
    init_db()
    print(f"✅ 登录成功：{client.user}")
    count = 0
    for guild in client.guilds:
        print(f"服务器：{guild.name}")
        for channel in guild.channels:
            if isinstance(channel, discord.ForumChannel):
                print(f"  论坛频道：{channel.name}")
                for thread in channel.threads:
                    tid = str(thread.id)
                    if not has_panel(tid):
                        print(f"    补面板：{thread.name}")
                        embed = discord.Embed(
                            title="📦 资源面板",
                            description="点「获取文件」领取本帖资源。作者点「上传文件」添加新版本。",
                            color=0x5865F2
                        )
                        try:
                            panel_msg = await thread.send(embed=embed, view=ResourcePanelView())
                            set_panel(tid, str(panel_msg.id))
                            owner_id = thread.owner_id
                            if owner_id:
                                await thread.send(f"👋 <@{owner_id}> 本帖已添加资源面板，点「上传文件」分享你的资源吧！")
                            count += 1
                        except Exception as e:
                            print(f"    ❌ 失败：{e}")
    print(f"\n✅ 完成！共补了 {count} 个面板")
    await client.close()

client.run(DISCORD_TOKEN)
