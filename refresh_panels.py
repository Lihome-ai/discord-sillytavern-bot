"""补面板脚本（新版）：给所有旧帖子更新资源面板，带上新按钮"""
import asyncio
import discord
from discord import ui
from config import DISCORD_TOKEN, ROLE_AUTHOR, ROLE_ADMIN
from database import init_db, has_panel, set_panel, get_panel_message_id, get_files_by_thread, toggle_file_lock, set_file_deleted, set_delete_requested

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
client = discord.Client(intents=intents)

# 新的ResourcePanelView（带上所有按钮）
class ResourcePanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _is_author_or_admin(self, interaction: discord.Interaction) -> bool:
        author_role = discord.utils.get(interaction.guild.roles, name=ROLE_AUTHOR)
        admin_role = discord.utils.get(interaction.guild.roles, name=ROLE_ADMIN)
        return (author_role and author_role in interaction.user.roles) or \
               (admin_role and admin_role in interaction.user.roles)

    @ui.button(label="获取文件", style=discord.ButtonStyle.primary, emoji="📥", custom_id="panel_claim")
    async def claim_file(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        thread_id = str(interaction.channel.id)
        files = get_files_by_thread(thread_id)
        if not files:
            await interaction.followup.send("📭 本帖还没有上传文件", ephemeral=True)
            return
        await interaction.followup.send("📦 请选择要获取的文件：", ephemeral=True)

    @ui.button(label="上传文件", style=discord.ButtonStyle.success, emoji="📤", custom_id="panel_upload")
    async def upload_file(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._is_author_or_admin(interaction):
            await interaction.response.send_message("❌ 只有作者和管理组可以上传文件", ephemeral=True)
            return
        await interaction.response.send_message("✅ 已私信你，请在私信里发送文件", ephemeral=True)

    @ui.button(label="锁定/解锁", style=discord.ButtonStyle.secondary, emoji="🔒", custom_id="panel_lock")
    async def toggle_lock(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._is_author_or_admin(interaction):
            await interaction.response.send_message("❌ 只有作者和管理组可以锁定/解锁", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        thread_id = str(interaction.channel.id)
        files = get_files_by_thread(thread_id)
        if not files:
            await interaction.followup.send("📭 本帖还没有上传文件", ephemeral=True)
            return
        f = files[0]
        new_state = toggle_file_lock(f["file_id"])
        if new_state:
            await interaction.followup.send("🔒 已锁定！普通用户现在无法获取这个资源了", ephemeral=True)
        else:
            await interaction.followup.send("🔓 已解锁！普通用户现在可以获取这个资源了", ephemeral=True)

    @ui.button(label="申请删除", style=discord.ButtonStyle.danger, emoji="🗑️", custom_id="panel_delete")
    async def request_delete(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._is_author_or_admin(interaction):
            await interaction.response.send_message("❌ 只有作者和管理组可以申请删除", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        thread_id = str(interaction.channel.id)
        files = get_files_by_thread(thread_id)
        if not files:
            await interaction.followup.send("📭 本帖还没有上传文件", ephemeral=True)
            return
        f = files[0]
        guild = interaction.guild
        admin_role = discord.utils.get(guild.roles, name=ROLE_ADMIN)
        if not admin_role:
            await interaction.followup.send("❌ 找不到管理组身份组", ephemeral=True)
            return
        set_delete_requested(f["file_id"], "申请删除：" + f["filename"])
        sent_count = 0
        for member in admin_role.members:
            try:
                embed = discord.Embed(
                    title="🗑️ 删除申请",
                    description="用户 " + str(interaction.user.mention) + " 申请删除资源：" + f["filename"],
                    color=0xED4245
                )
                embed.add_field(name="帖子", value=interaction.channel.mention, inline=False)
                embed.add_field(name="文件名", value=f["filename"], inline=False)
                await member.send(embed=embed)
                sent_count += 1
            except:
                pass
        await interaction.followup.send("✅ 已向 " + str(sent_count) + " 位管理组成员发送删除申请，等待审核", ephemeral=True)


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
                    # 看看有没有旧面板
                    old_panel_id = get_panel_message_id(tid)
                    if old_panel_id:
                        print(f"    更新面板：{thread.name}")
                        try:
                            old_msg = await thread.fetch_message(int(old_panel_id))
                            embed = discord.Embed(
                                title="📦 资源面板",
                                description="点「获取文件」领取本帖资源。作者点「上传文件」添加新版本。",
                                color=0x5865F2
                            )
                            await old_msg.edit(embed=embed, view=ResourcePanelView())
                            count += 1
                        except Exception as e:
                            print(f"    ❌ 失败：{e}")
                    else:
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
    print(f"\n✅ 完成！共更新了 {count} 个面板")
    await client.close()

client.run(DISCORD_TOKEN)
