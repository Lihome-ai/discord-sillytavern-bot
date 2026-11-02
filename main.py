"""灵蝶 Bot - 主程序入口"""
import os
import random
import hashlib
import asyncio
from datetime import datetime, timedelta, timezone
import discord
from discord import ui
from discord.ext import tasks
from config import (
    DISCORD_TOKEN, ROLE_NEWBIE, ROLE_REVIEWED, ROLE_REVIEWER, ROLE_ADMIN, ROLE_AUTHOR, ROLE_ABYSS,
    REVIEW_NOTICE, QUESTIONS, AGE_VERIFY_TASK, ANSWER_TIMEOUT,
    REVIEWER_DM_MESSAGES, ARCHIVE_CATEGORY_NAME
)
from database import (
    init_db, record_new_member, mark_got_newbie_role,
    create_ticket, get_open_ticket, update_ticket_status, get_ticket_by_channel,
    mark_ticket_started, get_expired_answering_tickets,
    get_expired_newbie_users, clear_newbie_flag,
    has_panel, set_panel, get_panel_message_id, add_resource_file, get_files_by_thread, get_file,
    add_claim, get_recent_claims
)
from watermark import add_watermark

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

client = discord.Client(intents=intents)

CATEGORY_NAME = "审核工单"  # 所有私密审核频道放在这个分类下


# ============ 欢迎消息按钮（用户用） ============

class WelcomeView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="领取新人身份", style=discord.ButtonStyle.success, emoji="🎫", custom_id="grab_newbie")
    async def grab_newbie(self, interaction: discord.Interaction, button: ui.Button):
        guild = interaction.guild or (interaction.client.guilds[0] if interaction.client.guilds else None)
        if guild is None:
            await interaction.response.send_message("❌ Bot 没有加入任何服务器。", ephemeral=True)
            return

        role_newbie = discord.utils.get(guild.roles, name=ROLE_NEWBIE)
        role_reviewed = discord.utils.get(guild.roles, name=ROLE_REVIEWED)
        if role_newbie is None:
            await interaction.response.send_message(f"❌ 服务器里没有「{ROLE_NEWBIE}」身份组。", ephemeral=True)
            return

        member = interaction.user
        if interaction.guild is None:
            member = guild.get_member(interaction.user.id)
            if member is None:
                await interaction.response.send_message("❌ 找不到你在服务器里的资料。", ephemeral=True)
                return

        if role_reviewed and role_reviewed in member.roles:
            await interaction.response.send_message("你已经是正式成员了，不需要领新人身份。", ephemeral=True)
            return
        if role_newbie in member.roles:
            await interaction.response.send_message("你已经领过新人身份了。", ephemeral=True)
            return

        try:
            await member.add_roles(role_newbie, reason="用户自助领取新人身份")
            record_new_member(str(member.id))
            mark_got_newbie_role(str(member.id))
            await interaction.response.send_message(
                f"✅ 已给你「{ROLE_NEWBIE}」身份！缓冲期 3 天，期间可点「申请审核」开始答题。",
                ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message("❌ Bot 权限不足，无法加身份。", ephemeral=True)

    @ui.button(label="申请审核", style=discord.ButtonStyle.primary, emoji="📝", custom_id="apply_review")
    async def apply_review(self, interaction: discord.Interaction, button: ui.Button):
        guild = interaction.guild or (interaction.client.guilds[0] if interaction.client.guilds else None)
        if guild is None:
            await interaction.response.send_message("❌ Bot 没有加入任何服务器。", ephemeral=True)
            return

        member = guild.get_member(interaction.user.id) if interaction.guild is None else interaction.user
        if member is None:
            await interaction.response.send_message("❌ 找不到你在服务器里的资料。", ephemeral=True)
            return

        existing = get_open_ticket(str(member.id))
        if existing:
            await interaction.response.send_message(
                f"你已经有一个进行中的工单（状态：{existing['status']}），请在对应频道里继续操作。",
                ephemeral=True
            )
            return

        role_newbie = discord.utils.get(guild.roles, name=ROLE_NEWBIE)
        if role_newbie not in member.roles:
            await interaction.response.send_message(
                f"你需要先领取「{ROLE_NEWBIE}」身份才能申请审核。", ephemeral=True
            )
            return

        reviewer_role = discord.utils.get(guild.roles, name=ROLE_REVIEWER)
        if reviewer_role is None:
            await interaction.response.send_message(f"❌ 服务器里没有「{ROLE_REVIEWER}」身份组。", ephemeral=True)
            return

        # 找/建"审核工单"分类
        category = discord.utils.get(guild.categories, name=CATEGORY_NAME)
        if category is None:
            category = await guild.create_category(CATEGORY_NAME)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True, attach_files=True
            ),
            reviewer_role: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            ),
        }
        channel = await guild.create_text_channel(
            name=f"审核-{member.name}",
            category=category,
            overwrites=overwrites,
            reason=f"用户 {member} 申请审核"
        )

        create_ticket(str(member.id), str(channel.id))

        embed = discord.Embed(
            title="📝 审核工单",
            description=REVIEW_NOTICE,
            color=0xFEE75C
        )
        await channel.send(
            content=f"{member.mention} 审核组 {reviewer_role.mention}",
            embed=embed,
            view=UserAnswerView()  # 只有用户按钮
        )
        await interaction.response.send_message(
            f"✅ 已为你创建私密审核频道：{channel.mention}",
            ephemeral=True
        )


# ============ 用户答题按钮 ============

class UserAnswerView(ui.View):
    """频道里只放给用户的按钮：开始答题 / 提交工单。"""
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="开始答题", style=discord.ButtonStyle.success, emoji="🚀", custom_id="start_answer")
    async def start_answer(self, interaction: discord.Interaction, button: ui.Button):
        embed = discord.Embed(
            title="📋 题目（限时 3 小时）",
            color=0x5865F2
        )
        for i, q in enumerate(QUESTIONS, 1):
            embed.add_field(name=f"主观题 {i}", value=q, inline=False)
        embed.add_field(name="成女验证题", value=AGE_VERIFY_TASK, inline=False)
        embed.set_footer(text="在本频道直接回复答案、上传视频即可。答完点「提交工单」。3 小时后自动提交。")
        await interaction.response.send_message(embed=embed)

        ticket = get_open_ticket(str(interaction.user.id))
        if ticket:
            update_ticket_status(ticket["ticket_id"], "answering")
            mark_ticket_started(ticket["ticket_id"])

    @ui.button(label="提交工单", style=discord.ButtonStyle.danger, emoji="📮", custom_id="submit_ticket")
    async def submit_ticket(self, interaction: discord.Interaction, button: ui.Button):
        ticket = get_open_ticket(str(interaction.user.id))
        if not ticket:
            await interaction.response.send_message("❌ 找不到你的工单记录。", ephemeral=True)
            return
        await interaction.response.send_message(
            "📮 工单已提交，你的发言权限已锁定，审核组会尽快审查。结果会通过私信通知你。"
        )
        await auto_submit_ticket(interaction.channel, ticket, auto=False)


# ============ 驳回后 DM 里的按钮（重新领身份 + 重新申请审核） ============

class ReapplyView(ui.View):
    """发给被驳回用户的 DM，带两个按钮：重新领身份 / 重新申请审核。"""
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="重新领取未过审身份", style=discord.ButtonStyle.success, emoji="🎫", custom_id="regrab_newbie")
    async def regrab_newbie(self, interaction: discord.Interaction, button: ui.Button):
        guild = client.guilds[0] if client.guilds else None
        if guild is None:
            await interaction.response.send_message("❌ Bot 没有加入任何服务器。", ephemeral=True)
            return
        member = guild.get_member(interaction.user.id)
        role_newbie = discord.utils.get(guild.roles, name=ROLE_NEWBIE)
        if member is None or role_newbie is None:
            await interaction.response.send_message("❌ 找不到身份组或你不在服务器。", ephemeral=True)
            return
        try:
            await member.add_roles(role_newbie, reason="驳回后重新领取新人身份")
            record_new_member(str(member.id))
            mark_got_newbie_role(str(member.id))
            await interaction.response.send_message("✅ 已重新领取「未过审」身份，3 天缓冲期重新开始。可以点下面的「重新申请审核」。")
        except discord.Forbidden:
            await interaction.response.send_message("❌ Bot 权限不足，无法加身份。", ephemeral=True)

    @ui.button(label="重新申请审核", style=discord.ButtonStyle.primary, emoji="📝", custom_id="reapply_review")
    async def reapply_review(self, interaction: discord.Interaction, button: ui.Button):
        guild = client.guilds[0] if client.guilds else None
        if guild is None:
            await interaction.response.send_message("❌ Bot 没有加入任何服务器。", ephemeral=True)
            return
        member = guild.get_member(interaction.user.id)
        if member is None:
            await interaction.response.send_message("❌ 你不在服务器里。", ephemeral=True)
            return

        role_newbie = discord.utils.get(guild.roles, name=ROLE_NEWBIE)
        if role_newbie not in member.roles:
            await interaction.response.send_message("⚠️ 你还没有「未过审」身份，请先点上面的「重新领取未过审身份」。", ephemeral=True)
            return

        existing = get_open_ticket(str(member.id))
        if existing:
            await interaction.response.send_message(f"你已经有一个进行中的工单（状态：{existing['status']}），请先去对应频道处理。", ephemeral=True)
            return

        reviewer_role = discord.utils.get(guild.roles, name=ROLE_REVIEWER)
        if reviewer_role is None:
            await interaction.response.send_message("❌ 服务器里没有「审核组」身份组。", ephemeral=True)
            return

        category = discord.utils.get(guild.categories, name=CATEGORY_NAME)
        if category is None:
            category = await guild.create_category(CATEGORY_NAME)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
            reviewer_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }
        channel = await guild.create_text_channel(
            name=f"审核-{member.name}", category=category, overwrites=overwrites,
            reason=f"用户 {member} 驳回后重新申请审核"
        )
        create_ticket(str(member.id), str(channel.id))
        embed = discord.Embed(title="📝 审核工单", description=REVIEW_NOTICE, color=0xFEE75C)
        await channel.send(content=f"{member.mention} 审核组 {reviewer_role.mention}", embed=embed, view=UserAnswerView())
        await interaction.response.send_message(f"✅ 已为你创建新的私密审核频道：{channel.mention}")


# ============ 身份领取频道 ============

IDENTITY_CHANNEL_NAME = "身份领取"
BACKUP_CHANNEL_NAME = "管理组备份"
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


class AbyssConfirmView(ui.View):
    """深渊区申请的二次确认按钮（ephemeral 消息里）。"""
    def __init__(self):
        super().__init__(timeout=60)

    @ui.button(label="我已确认，申请深渊身份", style=discord.ButtonStyle.danger, emoji="🌑")
    async def confirm_abyss(self, interaction: discord.Interaction, button: ui.Button):
        guild = interaction.guild or (client.guilds[0] if client.guilds else None)
        if guild is None:
            await interaction.response.send_message("❌ Bot 没有加入任何服务器。", ephemeral=True)
            return
        member = guild.get_member(interaction.user.id)
        role_abyss = discord.utils.get(guild.roles, name=ROLE_ABYSS)
        if member is None or role_abyss is None:
            await interaction.response.send_message("❌ 找不到身份组。", ephemeral=True)
            return
        if role_abyss in member.roles:
            await interaction.response.send_message("你已经是深渊者了。", ephemeral=True)
            return
        try:
            await member.add_roles(role_abyss, reason="用户自助申请深渊区")
            await interaction.response.edit_message(
                embed=discord.Embed(title="🌑 欢迎进入深渊", description="已授予「深渊者」身份，注意遵守社区规则，不外传、不ky。", color=0x2C2F33),
                view=None
            )
        except discord.Forbidden:
            await interaction.response.send_message("❌ Bot 权限不足。", ephemeral=True)


class IdentityClaimView(ui.View):
    """身份领取频道里的两个大按钮。"""
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="申请成为作者", style=discord.ButtonStyle.success, emoji="🎨", custom_id="apply_author")
    async def apply_author(self, interaction: discord.Interaction, button: ui.Button):
        guild = interaction.guild or (client.guilds[0] if client.guilds else None)
        if guild is None:
            await interaction.response.send_message("❌ Bot 没有加入任何服务器。", ephemeral=True)
            return
        member = guild.get_member(interaction.user.id)
        role_reviewed = discord.utils.get(guild.roles, name=ROLE_REVIEWED)
        role_author = discord.utils.get(guild.roles, name=ROLE_AUTHOR)
        if member is None or role_author is None:
            await interaction.response.send_message("❌ 找不到身份组。", ephemeral=True)
            return
        if role_reviewed and role_reviewed not in member.roles:
            await interaction.response.send_message("⚠️ 你还不是「过审社员」，先完成审核再来哦。", ephemeral=True)
            return
        if role_author in member.roles:
            await interaction.response.send_message("你已经是作者了，去发布你的第一张角色卡吧。", ephemeral=True)
            return
        try:
            await member.add_roles(role_author, reason="用户自助申请成为作者")
            await interaction.response.send_message(
                "🎉 恭喜成为作者！现在你可以发布自己的角色卡资源，被整个社区看到，积累你的创作者声望。",
                ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message("❌ Bot 权限不足。", ephemeral=True)

    @ui.button(label="申请进入深渊", style=discord.ButtonStyle.secondary, emoji="🌑", custom_id="apply_abyss")
    async def apply_abyss(self, interaction: discord.Interaction, button: ui.Button):
        guild = interaction.guild or (client.guilds[0] if client.guilds else None)
        if guild is None:
            await interaction.response.send_message("❌ Bot 没有加入任何服务器。", ephemeral=True)
            return
        member = guild.get_member(interaction.user.id)
        role_abyss = discord.utils.get(guild.roles, name=ROLE_ABYSS)
        if member is None or role_abyss is None:
            await interaction.response.send_message("❌ 服务器里还没有「深渊者」身份组，请先联系管理员创建。", ephemeral=True)
            return
        if role_abyss in member.roles:
            await interaction.response.send_message("你已经是深渊者了。", ephemeral=True)
            return

        embed = discord.Embed(
            title="🌑 欢迎来到深渊角色卡区域",
            description=(
                "入区即视作已完整阅读并同意本区所有规则。\n"
                "进入此区，默认你已成年，并接受本区包含的一切可能引起生理/心理不适的内容。\n"
                "**所有卡片严禁以任何形式转载、截图、分享至区外。**"
            ),
            color=0x2C2F33
        )
        embed.add_field(
            name="📜 玩家守则",
            value=(
                "1. 首楼点赞，取卡留痕\n"
                "2. 所有角色卡只能在 SillyTavern 非商业平台游玩（安卓本地酒馆 / iOS 云酒馆，**不能用付费云酒馆**）；禁止二传、二改（作者无标注时默认遵循）、商用、搬运至商业化平台\n"
                "3. 尊重一切主观 repo，严禁因本区内容产生的不适而攻击作者及角色\n"
                "4. 为爱发电，作者与玩家无消费关系，禁止发布对作者/角色不满的言论\n"
                "5. 因 XP 不同而发布对作者/角色的不友好言论\n"
                "6. 禁止刷屏、水贴"
            ),
            inline=False
        )
        embed.add_field(
            name="💬 发布评论规范",
            value=(
                "• 发布暗示/明示的图片文字，须加防剧透模式\n"
                "• 包含剧情、人设等剧透内容，须加防剧透模式\n"
                "• 发布可能引起不适的图文，须自行标注预警标签并善用防剧透模式"
            ),
            inline=False
        )
        embed.add_field(
            name="✍️ 作者守则",
            value=(
                "本区为女性向无限制创作区，不限创作内容，默认接受一切非大众性癖与硬核内容。\n"
                "在本区发卡即默认接受本规定。\n"
                "**禁止涉及任何真实政治红线、历史痛点、真人真事**，如有此类作品将永久封禁。"
            ),
            inline=False
        )
        embed.add_field(
            name="🩸 深渊区内容规范",
            value=(
                "1. 本区为 G 向、重口、女绿、虐身、精神污染等非大众/硬核内容专属发布区\n"
                "2. 发帖标题最前须标明核心标签，如【G向】【重口】【女绿】【虐身】等\n"
                "3. 必须善用标签系统，尽可能详细标注具体可能引起不适的元素\n"
                "4. 在卡内或首楼进行预警\n"
                "5. 禁止墙内引流"
            ),
            inline=False
        )
        embed.set_footer(text="阅读完毕，同意所有规则后点下面按钮。")
        await interaction.response.send_message(embed=embed, view=AbyssConfirmView(), ephemeral=True)


async def setup_identity_channel():
    """on_ready 时找/建身份领取频道，发说明消息。仅过审社员可见。"""
    for guild in client.guilds:
        role_reviewed = discord.utils.get(guild.roles, name=ROLE_REVIEWED)
        channel = discord.utils.get(guild.text_channels, name=IDENTITY_CHANNEL_NAME)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        if role_reviewed:
            overwrites[role_reviewed] = discord.PermissionOverwrite(view_channel=True, send_messages=False)

        if channel is None:
            try:
                channel = await guild.create_text_channel(IDENTITY_CHANNEL_NAME, overwrites=overwrites)
            except discord.Forbidden:
                continue
        else:
            try:
                for target, perm in overwrites.items():
                    await channel.set_permissions(target, overwrite=perm)
            except discord.Forbidden:
                pass

        embed = discord.Embed(
            title="🎖️ 身份自助领取区",
            description=(
                "**想当作者？**\n"
                "发布你的角色卡，让整个社区看到你的创作。被喜欢、被使用、被记住——这里是你的作品展示柜。\n\n"
                "**想进深渊？**\n"
                "那里藏着更暗黑、更浓烈的故事。成年社员专属，进去前请确认你准备好了。\n\n"
                "👇 点下面的按钮自助申请。"
            ),
            color=0x5865F2
        )
        try:
            async for msg in channel.history(limit=10):
                if msg.author == client.user and msg.embeds and "身份自助领取区" in msg.embeds[0].title:
                    return
        except discord.Forbidden:
            pass
        try:
            await channel.send(embed=embed, view=IdentityClaimView())
        except discord.Forbidden:
            pass


# ============ 资源上传频道初始化 ============

async def setup_resource_channels():
    """建/找管理组备份频道（只管理组可见，作者不知道）。"""
    for guild in client.guilds:
        role_admin = discord.utils.get(guild.roles, name=ROLE_ADMIN)
        backup_channel = discord.utils.get(guild.text_channels, name=BACKUP_CHANNEL_NAME)
        backup_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        if role_admin:
            backup_overwrites[role_admin] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )
        if backup_channel is None:
            try:
                await guild.create_text_channel(BACKUP_CHANNEL_NAME, overwrites=backup_overwrites)
            except discord.Forbidden:
                pass


# ============ 审核归档辅助 ============

async def archive_ticket(channel: discord.TextChannel, target: discord.Member, reviewer: discord.Member, result_text: str):
    """审核完成后：把频道移到审核归档分类，关闭被审核用户可见权限，DM 审核员留痕提醒。"""
    guild = channel.guild

    # 找/建归档分类
    archive_cat = discord.utils.get(guild.categories, name=ARCHIVE_CATEGORY_NAME)
    if archive_cat is None:
        try:
            archive_cat = await guild.create_category(ARCHIVE_CATEGORY_NAME)
        except discord.Forbidden:
            pass

    # 移动频道到归档分类
    if archive_cat:
        try:
            await channel.edit(category=archive_cat)
        except discord.Forbidden:
            pass

    # 关闭被审核用户的可见权限
    try:
        await channel.set_permissions(target, view_channel=False, send_messages=False)
    except discord.Forbidden:
        pass

    # 随机选一条文案 DM 给审核员
    msg = random.choice(REVIEWER_DM_MESSAGES).format(
        user_id=str(target.id),
        result=result_text
    )
    try:
        await reviewer.send(msg)
    except discord.Forbidden:
        pass


# ============ 审核员按钮 ============

class ReviewerControlView(ui.View):
    """审核员专用按钮：通过/驳回。非审核组点了会被拒绝。"""
    def __init__(self):
        super().__init__(timeout=None)

    async def _is_reviewer(self, interaction: discord.Interaction) -> bool:
        reviewer_role = discord.utils.get(interaction.guild.roles, name=ROLE_REVIEWER)
        return reviewer_role is not None and reviewer_role in interaction.user.roles

    def _disable_buttons(self):
        for child in self.children:
            child.disabled = True

    @ui.button(label="审核通过", style=discord.ButtonStyle.success, emoji="✅", custom_id="approve_ticket")
    async def approve_ticket(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._is_reviewer(interaction):
            await interaction.response.send_message("❌ 只有审核组能操作这个按钮。", ephemeral=True)
            return

        ticket = get_ticket_by_channel(str(interaction.channel.id))
        if not ticket:
            await interaction.response.send_message("❌ 找不到工单记录。", ephemeral=True)
            return
        if ticket["status"] in ("approved", "rejected"):
            await interaction.response.send_message(f"⚠️ 这个工单已经处理过了（当前状态：{ticket['status']}）。", ephemeral=True)
            return

        target = interaction.guild.get_member(int(ticket["user_id"]))
        if target is None:
            await interaction.response.send_message("❌ 该用户已不在服务器。", ephemeral=True)
            return

        role_reviewed = discord.utils.get(interaction.guild.roles, name=ROLE_REVIEWED)
        role_newbie = discord.utils.get(interaction.guild.roles, name=ROLE_NEWBIE)
        if role_reviewed is None:
            await interaction.response.send_message("❌ 服务器里没有「过审社员」身份组。", ephemeral=True)
            return

        try:
            await target.add_roles(role_reviewed, reason=f"审核通过，审核员：{interaction.user}")
            if role_newbie and role_newbie in target.roles:
                await target.remove_roles(role_newbie, reason="审核通过，自动移除未过审")
            update_ticket_status(ticket["ticket_id"], "approved")
            self._disable_buttons()
            await interaction.response.edit_message(view=self)
            await interaction.followup.send(
                f"✅ 已通过 {target.mention}（审核员：{interaction.user}），授予「{ROLE_REVIEWED}」，移除「{ROLE_NEWBIE}」。"
            )
            try:
                await target.send("🎉 恭喜，你的审核已通过！已授予「过审社员」身份，欢迎正式加入社区。")
            except discord.Forbidden:
                pass
            await archive_ticket(interaction.channel, target, interaction.user, "通过")
        except discord.Forbidden:
            await interaction.response.send_message("❌ Bot 权限不足，无法加身份。", ephemeral=True)

    @ui.button(label="审核驳回", style=discord.ButtonStyle.secondary, emoji="❌", custom_id="reject_ticket")
    async def reject_ticket(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._is_reviewer(interaction):
            await interaction.response.send_message("❌ 只有审核组能操作这个按钮。", ephemeral=True)
            return

        ticket = get_ticket_by_channel(str(interaction.channel.id))
        if not ticket:
            await interaction.response.send_message("❌ 找不到工单记录。", ephemeral=True)
            return
        if ticket["status"] in ("approved", "rejected"):
            await interaction.response.send_message(f"⚠️ 这个工单已经处理过了（当前状态：{ticket['status']}）。", ephemeral=True)
            return

        target = interaction.guild.get_member(int(ticket["user_id"]))
        role_newbie = discord.utils.get(interaction.guild.roles, name=ROLE_NEWBIE)

        if target and role_newbie and role_newbie in target.roles:
            try:
                await target.remove_roles(role_newbie, reason="审核驳回，移除未过审身份，需重新领取")
            except discord.Forbidden:
                pass

        update_ticket_status(ticket["ticket_id"], "rejected")
        self._disable_buttons()
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            f"❌ 已驳回 {target.mention if target else '该用户'}（审核员：{interaction.user}），已移除其「{ROLE_NEWBIE}」身份，可重新领取后再次申请。"
        )
        if target:
            try:
                embed = discord.Embed(
                    title="😢 审核未通过",
                    description="你的审核未通过，「未过审」身份已被移除。\n点下面的按钮重新领取身份并再次申请。",
                    color=0xED4245
                )
                await target.send(embed=embed, view=ReapplyView())
            except discord.Forbidden:
                pass
            await archive_ticket(interaction.channel, target, interaction.user, "驳回")


# ============ 自动提交超时工单（3 小时答题到期） ============

async def auto_submit_ticket(channel: discord.TextChannel, ticket: dict, auto: bool = False):
    """把工单状态改成 submitted，锁定用户发言，发审核操作区。手动/自动都调用这个。"""
    update_ticket_status(ticket["ticket_id"], "submitted")
    guild = channel.guild
    target = guild.get_member(int(ticket["user_id"]))

    if target:
        try:
            await channel.set_permissions(target, send_messages=False)
        except discord.Forbidden:
            pass

    if auto:
        await channel.send("⏰ 答题时间到（3 小时），已自动提交工单。")
    await channel.send(
        content="—— 审核操作区（仅审核组可操作）——",
        view=ReviewerControlView()
    )


@tasks.loop(minutes=5)
async def auto_submit_expired_tickets():
    expired = get_expired_answering_tickets(hours=3)
    for ticket in expired:
        channel = client.get_channel(int(ticket["channel_id"]))
        if channel is None:
            continue
        try:
            await auto_submit_ticket(channel, ticket, auto=True)
            print(f"⏰ 自动提交超时工单：ticket #{ticket['ticket_id']} user={ticket['user_id']}")
        except Exception as e:
            print(f"⚠️ 自动提交工单失败 ticket #{ticket['ticket_id']}: {e}")


# ============ 自动清理 3 天缓冲期到期的未过审身份 ============

@tasks.loop(hours=12)
async def expire_newbie_roles():
    expired_users = get_expired_newbie_users(days=3)
    for row in expired_users:
        user_id = row["user_id"]
        for guild in client.guilds:
            member = guild.get_member(int(user_id))
            if member is None:
                continue
            role_newbie = discord.utils.get(guild.roles, name=ROLE_NEWBIE)
            role_reviewed = discord.utils.get(guild.roles, name=ROLE_REVIEWED)
            if role_newbie is None:
                continue
            # 已经过审的用户不动
            if role_reviewed and role_reviewed in member.roles:
                clear_newbie_flag(user_id)
                continue
            if role_newbie not in member.roles:
                # 身份已经不在了，清掉标记
                clear_newbie_flag(user_id)
                continue
            try:
                await member.remove_roles(role_newbie, reason="3 天缓冲期到期，自动移除未过审身份")
                clear_newbie_flag(user_id)
                print(f"🗓️  缓冲期到期，移除 {member} 的未过审身份")
                try:
                    await member.send(
                        "⌛ 你的 3 天缓冲期已结束，「未过审」身份已被移除。"
                        "如果还想加入社区，可以重新领取「未过审」身份后再次申请审核。"
                    )
                except discord.Forbidden:
                    pass
            except discord.Forbidden:
                print(f"⚠️ 没有权限移除 {member} 的身份")


# ============ 定时清理归档频道（24 小时自动删除） ============

@tasks.loop(hours=1)
async def cleanup_archived_channels():
    now = datetime.now(timezone.utc)
    for guild in client.guilds:
        archive_cat = discord.utils.get(guild.categories, name=ARCHIVE_CATEGORY_NAME)
        if archive_cat is None:
            continue
        for channel in archive_cat.text_channels:
            age = now - channel.created_at
            if age > timedelta(hours=24):
                try:
                    await channel.delete(reason="归档超过 24 小时，自动清理")
                    print(f"🗑️  已自动删除归档频道：{channel.name}")
                except discord.Forbidden:
                    print(f"⚠️  没有权限删除频道：{channel.name}")


@cleanup_archived_channels.before_loop
async def before_cleanup():
    await client.wait_until_ready()


@auto_submit_expired_tickets.before_loop
async def before_auto_submit():
    await client.wait_until_ready()


@expire_newbie_roles.before_loop
async def before_expire_newbie():
    await client.wait_until_ready()


# ============ 事件 ============

@client.event
async def on_ready():
    init_db()
    print(f"✅ 登录成功！已登录为：{client.user}")
    print(f"Bot  ID：{client.user.id}")
    # 注册持久化 View（Bot 重启后旧按钮仍可用）
    client.add_view(ResourcePanelView())
    client.add_view(WelcomeView())
    client.add_view(ReviewerControlView())
    client.add_view(IdentityClaimView())
    if not cleanup_archived_channels.is_running():
        cleanup_archived_channels.start()
    if not auto_submit_expired_tickets.is_running():
        auto_submit_expired_tickets.start()
    if not expire_newbie_roles.is_running():
        expire_newbie_roles.start()
    print("定时任务已启动：归档清理(24h) / 答题自动提交(3h) / 缓冲期清理(3d)")
    await setup_identity_channel()
    await setup_resource_channels()
    print("按 Ctrl+C 停止。")


_panel_pending = set()  # 内存锁：正在处理中的 thread_id
_upload_sessions = {}   # user_id -> thread_id（正在上传的会话）


class FileSelectView(ui.View):
    """用户点获取按钮后弹出的文件选择菜单（临时，3分钟过期）。"""
    def __init__(self, files):
        super().__init__(timeout=180)
        options = []
        for f in files:
            options.append(discord.SelectOption(
                label=f"{f['version'] or '无版本'} - {f['filename']}",
                value=str(f['file_id'])
            ))
        self.select = ui.Select(placeholder="请选择要获取的文件...", options=options, custom_id="file_select")
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        # 先 defer，避免 3 秒超时（加水印、发文件等耗时操作）
        await interaction.response.defer(ephemeral=True)
        file_id = int(self.select.values[0])
        f = get_file(file_id)
        if not f:
            await interaction.followup.send("❌ 文件不存在", ephemeral=True)
            return
        # 频率检查
        if get_recent_claims(str(interaction.user.id), minutes=5) >= 10:
            await interaction.followup.send("⏰ 领取太频繁了，5 分钟后再试", ephemeral=True)
            return
        try:
            # 加水印：文件名加用户ID + 图片/文本加隐形水印
            watermarked_path, new_filename = add_watermark(
                f["file_path"], str(interaction.user.id), f["filename"]
            )
            file = discord.File(watermarked_path, filename=new_filename)
            await interaction.user.send(
                f"📦 你领取了资源：{f['filename']}（版本：{f['version'] or '无'}）",
                file=file
            )
            add_claim(f['file_id'], str(interaction.user.id))
            # 删除临时文件
            try:
                os.unlink(watermarked_path)
            except:
                pass
            await interaction.followup.send("✅ 已通过私信发送文件", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ 无法私信你，请先打开 DM 权限", ephemeral=True)


class ResourcePanelView(ui.View):
    """资源帖面板：获取文件（所有人，需点赞）+ 上传文件（作者/管理组）。"""
    def __init__(self):
        super().__init__(timeout=None)

    async def _is_author_or_admin(self, interaction: discord.Interaction) -> bool:
        author_role = discord.utils.get(interaction.guild.roles, name=ROLE_AUTHOR)
        admin_role = discord.utils.get(interaction.guild.roles, name=ROLE_ADMIN)
        return (author_role and author_role in interaction.user.roles) or \
               (admin_role and admin_role in interaction.user.roles)

    async def _has_reacted(self, thread: discord.Thread, user: discord.User) -> bool:
        """检查用户是否给帖子首楼点了任何反应。"""
        try:
            # 找帖子的第一条消息（首楼）
            async for message in thread.history(limit=1, oldest_first=True):
                # 检查这条消息的所有反应，用户点了任何一个就算
                for reaction in message.reactions:
                    users = [u async for u in reaction.users()]
                    if user in users:
                        return True
                return False
            return False
        except Exception as e:
            print(f"   检查反应失败：{e}")
            return False

    @ui.button(label="获取文件", style=discord.ButtonStyle.primary, emoji="📥", custom_id="panel_claim")
    async def claim_file(self, interaction: discord.Interaction, button: ui.Button):
        print(f"🔘 获取文件按钮被点击：{interaction.user}")
        # 先 defer，避免 3 秒超时（点赞检查等耗时操作会超时）
        await interaction.response.defer(ephemeral=True)
        thread_id = str(interaction.channel.id)
        files = get_files_by_thread(thread_id)
        print(f"   文件列表：{files}")
        if not files:
            await interaction.followup.send("📭 本帖还没有上传文件", ephemeral=True)
            return
        # 选最新版本
        f = files[0]
        # 频率检查
        if get_recent_claims(str(interaction.user.id), minutes=5) >= 10:
            await interaction.followup.send("⏰ 领取太频繁了，5 分钟后再试", ephemeral=True)
            return
        # 检查是否点了反应
        if not await self._has_reacted(interaction.channel, interaction.user):
            await interaction.followup.send("❗ 请先给帖子首楼点个反应（任何 emoji 都行），再领取文件哦！", ephemeral=True)
            return
        # 点赞了，发文件选择菜单
        await interaction.followup.send("📦 请选择要获取的文件：", view=FileSelectView(files), ephemeral=True)

    @ui.button(label="上传文件", style=discord.ButtonStyle.success, emoji="📤", custom_id="panel_upload")
    async def upload_file(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._is_author_or_admin(interaction):
            await interaction.response.send_message("❌ 只有作者和管理组可以上传文件", ephemeral=True)
            return
        thread_id = str(interaction.channel.id)
        _upload_sessions[str(interaction.user.id)] = thread_id
        await interaction.response.defer(ephemeral=True)
        try:
            await interaction.user.send(
                f"📤 请发送要上传的文件。\n"
                f"附件说明：发文件时在同一条消息里写「版本名」\n"
                f"例如：v1.0"
            )
            await interaction.followup.send("✅ 已私信你，请在私信里发送文件", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ 无法私信你，请先打开 DM 权限", ephemeral=True)


@client.event
async def on_thread_create(thread: discord.Thread):
    print(f"🧵 新 thread 创建：{thread.name}，父频道：{thread.parent}，ID：{thread.id}")
    if not isinstance(thread.parent, discord.ForumChannel):
        print("   不是 Forum 频道，跳过")
        return
    tid = str(thread.id)
    # 延迟 2 秒，等事件完全处理完
    await asyncio.sleep(2)
    if has_panel(tid):
        print("   已有面板，跳过")
        return
    embed = discord.Embed(
        title="📦 资源面板",
        description="点「获取文件」领取本帖资源。作者点「上传文件」添加新版本。",
        color=0x5865F2
    )
    try:
        panel_msg = await thread.send(embed=embed, view=ResourcePanelView())
        set_panel(tid, str(panel_msg.id))
        print(f"✅ 已在帖子 {thread.name} 发面板")
    except Exception as e:
        print(f"❌ 发面板失败：{e}")


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # DM 里的文件上传处理
    if isinstance(message.channel, discord.DMChannel) and message.attachments:
        uid = str(message.author.id)
        # 先检查：是不是管理组发文件来追溯的？
        is_admin = False
        for guild in client.guilds:
            admin_role = discord.utils.get(guild.roles, name=ROLE_ADMIN)
            member = guild.get_member(message.author.id)
            if member and admin_role and admin_role in member.roles:
                is_admin = True
                break
        if is_admin and uid not in _upload_sessions:
            # 管理组发文件 = 追溯水印
            att = message.attachments[0]
            try:
                # 下载文件到临时目录
                import tempfile
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(att.filename)[1])
                tmp.close()
                await att.save(tmp.name)
                # 读水印
                from trace import read_watermark, lookup_user
                user_id = read_watermark(tmp.name)
                os.unlink(tmp.name)
                if not user_id:
                    await message.channel.send("❌ 没找到水印，这个文件可能不是从本社区下载的。")
                    return
                # 查记录
                records = lookup_user(user_id)
                embed = discord.Embed(title="🔍 水印追溯结果", color=0xED4245)
                embed.add_field(name="下载用户ID", value=user_id, inline=False)
                embed.add_field(name="该用户最近领取记录", value="", inline=False)
                for i, (file_id, filename, version, claimed_at) in enumerate(records[:10], 1):
                    embed.add_field(
                        name=f"{i}. {claimed_at}",
                        value=f"文件：{filename}（版本：{version}）",
                        inline=False
                    )
                if not records:
                    embed.add_field(name="", value="（数据库里没有领取记录）", inline=False)
                await message.channel.send(embed=embed)
            except Exception as e:
                await message.channel.send(f"❌ 读取水印失败：{e}")
            return
        if uid in _upload_sessions:
            thread_id = _upload_sessions.pop(uid)
            att = message.attachments[0]
            # 解析消息内容：版本名
            version = message.content.strip() or "无版本"
            # 下载文件
            save_path = os.path.join(UPLOAD_DIR, f"{att.id}_{att.filename}")
            await att.save(save_path)
            add_resource_file(
                thread_id=thread_id,
                filename=att.filename,
                version=version,
                uploader_id=uid,
                password="",
                require_like=1,
                file_path=save_path
            )
            # 备份到管理组频道：只发链接，不转发图片本体
            for guild in client.guilds:
                backup_channel = discord.utils.get(guild.text_channels, name=BACKUP_CHANNEL_NAME)
                if backup_channel:
                    try:
                        embed = discord.Embed(title="📦 资源上传备份", color=0x2B2D31)
                        embed.add_field(name="帖子 ID", value=thread_id, inline=True)
                        embed.add_field(name="文件名", value=att.filename, inline=True)
                        embed.add_field(name="版本", value=version, inline=True)
                        embed.add_field(name="上传者", value=f"{message.author}（ID: {uid}）", inline=False)
                        embed.add_field(name="下载链接", value=att.url, inline=False)
                        await backup_channel.send(embed=embed)
                    except Exception as e:
                        print(f"备份频道发消息失败：{e}")
            await message.channel.send(
                f"✅ 文件已保存：{att.filename}（版本：{version}）"
            )
            return

    # Forum 帖子：只记录，不发面板（on_thread_create 负责发）
    if isinstance(message.channel, discord.Thread) and isinstance(message.channel.parent, discord.ForumChannel):
        print(f"📨 Forum 帖子消息：{message.channel.name}，ID：{message.channel.id}")
        # 检测"/回顶"命令
        if message.content.strip() == "/回顶":
            tid = str(message.channel.id)
            panel_msg_id = get_panel_message_id(tid)
            if panel_msg_id:
                # 构造 Discord 消息链接
                guild_id = message.guild.id
                thread_id = message.channel.id
                link = f"https://discord.com/channels/{guild_id}/{thread_id}/{panel_msg_id}"
                # 先删掉用户发的"/回顶"消息
                try:
                    await message.delete()
                except:
                    pass
                # 发带按钮的消息
                view = discord.ui.View()
                view.add_item(discord.ui.Button(label="🔝 回到首楼", url=link, style=discord.ButtonStyle.primary))
                msg = await message.channel.send("需要回顶？点按钮：", view=view)
                # 10秒后自动删除这条消息
                await asyncio.sleep(10)
                try:
                    await msg.delete()
                except:
                    pass
            else:
                await message.channel.send("❌ 这个帖子还没有资源面板")
        return

    # 普通文本频道消息（保留调试）
    print(f"📨 收到：{message.author} 在 #{message.channel} 说：{message.content}")

    # 管理组命令：/补面板 - 给旧帖子补资源面板
    if message.content.strip() == "/补面板":
        # 检查是不是管理组
        admin_role = discord.utils.get(message.guild.roles, name=ROLE_ADMIN)
        if not admin_role or admin_role not in message.author.roles:
            await message.channel.send("❌ 只有管理组能用这个命令")
            return
        await message.channel.send("🔄 开始扫描所有论坛帖子...")
        count = 0
        for channel in message.guild.channels:
            if isinstance(channel, discord.ForumChannel):
                # 遍历这个论坛的所有帖子
                for thread in channel.threads:
                    tid = str(thread.id)
                    if not has_panel(tid):
                        # 没有面板，补一个
                        embed = discord.Embed(
                            title="📦 资源面板",
                            description="点「获取文件」领取本帖资源。作者点「上传文件」添加新版本。",
                            color=0x5865F2
                        )
                        try:
                            panel_msg = await thread.send(embed=embed, view=ResourcePanelView())
                            set_panel(tid, str(panel_msg.id))
                            # @作者提醒上传
                            owner_id = thread.owner_id
                            if owner_id:
                                await thread.send(f"👋 <@{owner_id}> 本帖已添加资源面板，点「上传文件」分享你的资源吧！")
                            count += 1
                        except Exception as e:
                            print(f"给帖子 {thread.name} 补面板失败：{e}")
        await message.channel.send(f"✅ 完成！共给 {count} 个旧帖子补上了资源面板")


@client.event
async def on_member_join(member: discord.Member):
    if member.bot:
        return

    record_new_member(str(member.id))
    guild = member.guild

    dm_sent = False
    try:
        dm_embed = discord.Embed(
            title="👋 欢迎加入！",
            description=(
                f"欢迎来到 **{guild.name}**！\n\n"
                f"1️⃣ 点「领取新人身份」开始 3 天缓冲期\n"
                f"2️⃣ 点「申请审核」进入答题流程"
            ),
            color=0x5865F2
        )
        await member.send(embed=dm_embed, view=WelcomeView())
        dm_sent = True
    except (discord.Forbidden, discord.HTTPException):
        dm_sent = False

    channel = guild.system_channel
    if channel is None:
        return

    if dm_sent:
        notice = discord.Embed(
            description=f"👋 欢迎新成员 {member.mention}，欢迎消息和按钮已通过私信发给你。",
            color=0x5865F2
        )
        await channel.send(embed=notice)
    else:
        embed = discord.Embed(
            title="👋 欢迎加入！",
            description=(
                f"{member.mention}，欢迎来到 **{guild.name}**！\n\n"
                f"点下面的按钮领取新人身份，然后申请审核。"
            ),
            color=0x5865F2
        )
        await channel.send(content=member.mention, embed=embed, view=WelcomeView())


client.run(DISCORD_TOKEN)
