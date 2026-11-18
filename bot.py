"""Evoke Systems | Discord support and roster applications."""
import asyncio
import logging
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / ".packages"))

import discord
from aiohttp import web
from discord import ui

GUILD_ID = 1454719356226240517
CATEGORY = 1558547311590903831
STAFF = 1565166580411924610
FORMER_STAFF = 1560752530366144543
SUPPORT = 1566118218605662409
REQUIREMENTS = 1566120622294048819
BANNER = ROOT / "banner.webp"
PREFIX = "evoke:v1:"
log = logging.getLogger("evoke")
ticket_lock = asyncio.Lock()
publish_lock = asyncio.Lock()
client = discord.Client(intents=discord.Intents(guilds=True),
                        allowed_mentions=discord.AllowedMentions.none())


def button(label, action, style=discord.ButtonStyle.secondary):
    return ui.Button(label=label, custom_id=PREFIX + action, style=style)


def panel(*items):
    gallery = ui.MediaGallery(discord.MediaGalleryItem(
        media="attachment://banner.webp", description="Evoke Esports banner"))
    view = ui.LayoutView(timeout=None)
    view.add_item(ui.Container(gallery, *(ui.TextDisplay(item) if isinstance(item, str)
                                         else item for item in items)))
    return view


def public_panel(channel):
    if channel == SUPPORT:
        return panel("# Evoke Support", "Open a private ticket to reach the Evoke staff team. "
                     "For roster applications, include your division, profile link, and recent results.",
                     ui.ActionRow(button("Evoke Support", "open", discord.ButtonStyle.primary)))
    return panel("# Join Evoke", "## Competitive roster\nInclude **evk** in your username. "
                 "Find your division by Power Ranking Points (PR).",
                 "**Team Evoke**  ·  30,000+ PR\n**Evoke Competitive**  ·  10,000+ PR\n"
                 "**Evoke Semi Pro**  ·  5,000+ PR\n**Academy**  ·  3,000+ PR and consistently high event finishes\n"
                 "**Grinders**  ·  500–2,999+ PR\n**Future**  ·  0–500 PR",
                 ui.Separator(), "## Content roster\n**Main Content**  ·  10,000 followers or subscribers\n"
                 "Include **evk** on your social profiles.",
                 ui.Separator(), f"## How to apply\nGo to <#{SUPPORT}> and select **Evoke Support**. "
                 "Tell us which roster you’re applying for, link your profile, and explain why you’re a good fit.")


def controls(owner, claimant=0):
    state = f"Claimed by <@{claimant}>" if claimant else "Waiting for staff"
    return panel("# Evoke Support", f"**Opened by** <@{owner}>\n**Status** {state}",
                 "Describe what you need below. Applying to Evoke? Include your roster and profile link.",
                 ui.ActionRow(button("Claim ticket", "claim"), button("Close ticket", "close", discord.ButtonStyle.danger)))


def topic(owner, claimant=0, status="open"):
    return f"evoke-ticket/{owner}/{claimant}/{status}"


def ticket_data(channel):
    if not isinstance(channel, discord.TextChannel) or channel.category_id != CATEGORY:
        return None
    parts = (channel.topic or "").split("/")
    if len(parts) == 4 and parts[0] == "evoke-ticket" and all(x.isdecimal() for x in parts[1:3]):
        return int(parts[1]), int(parts[2]), parts[3]


def upload():
    return discord.File(BANNER, filename="banner.webp")


async def open_ticket(i):
    category = i.guild.get_channel(CATEGORY)
    role = i.guild.get_role(STAFF)
    if i.channel_id != SUPPORT or not isinstance(category, discord.CategoryChannel) or role is None:
        return "Staff should check the support channel, ticket category, and role IDs."
    async with ticket_lock:
        channels = await i.guild.fetch_channels()
        for channel in channels:
            data = ticket_data(channel)
            if data and data[0] == i.user.id and data[2] == "open":
                return f"Your open ticket is {channel.mention}."
        allow = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True,
                                           attach_files=True)
        overwrites = {i.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                      i.user: allow, role: allow, i.guild.me: allow}
        channel = await i.guild.create_text_channel(f"support-{i.user.id}", category=category,
                                                    overwrites=overwrites, topic=topic(i.user.id))
        try:
            await channel.send(view=controls(i.user.id), file=upload())
        except discord.HTTPException:
            await channel.delete(reason="Ticket controls failed to publish")
            raise
        try:
            await channel.send(f"{role.mention} · New Evoke Support ticket",
                               allowed_mentions=discord.AllowedMentions(roles=[role], users=False, everyone=False))
        except discord.HTTPException:
            log.exception("Staff notification failed for ticket %s", channel.id)
            return f"Your ticket is {channel.mention}. Staff can see it, but the notification failed."
        return f"Your ticket is ready in {channel.mention}."


async def manage_ticket(i, action):
    async with ticket_lock:
        channel = await i.guild.fetch_channel(i.channel_id)
        data = ticket_data(channel)
        if not data or data[2] != "open":
            return "This ticket is already closed or unavailable."
        owner, claimant, _ = data
        staff = any(role.id == STAFF for role in i.user.roles)
        if not staff and (action == "claim" or i.user.id != owner):
            return "Only Evoke staff can do that."
        if action == "claim":
            if claimant and claimant != i.user.id:
                return f"This ticket was claimed by <@{claimant}>."
            await channel.edit(topic=topic(owner, i.user.id))
            await i.message.edit(view=controls(owner, i.user.id))
            return "You claimed this ticket."
        if action in ("close", "confirm"):  # Older confirmations promised an archive.
            await i.edit_original_response(view=confirmation(), attachments=[upload()])
            return None
        if action == "delete":
            await i.edit_original_response(content="Deleting this ticket…")
            await channel.delete(reason=f"Evoke ticket closed by {i.user.id}")
            return None


def confirmation():
    return panel("## Close ticket?", "Confirming permanently deletes this channel and its message history.", ui.ActionRow(button("Delete ticket", "delete", discord.ButtonStyle.danger)))


@client.event
async def on_interaction(i):
    cid = (i.data or {}).get("custom_id", "")
    if i.guild_id != GUILD_ID or not cid.startswith(PREFIX):
        return
    await i.response.defer(ephemeral=True, thinking=True)
    try:
        action = cid[len(PREFIX):]
        result = await open_ticket(i) if action == "open" else await manage_ticket(i, action)
        if result:
            await i.followup.send(result, ephemeral=True)
    except discord.HTTPException:
        log.exception("Discord rejected %s in %s", cid, i.channel_id)
        await i.followup.send("Discord could not finish that action. Staff can check the bot permissions and service logs.",
                              ephemeral=True)


async def update_open_ticket_access():
    guild = client.get_guild(GUILD_ID)
    staff = guild.get_role(STAFF) if guild else None
    if staff is None:
        log.error("Evoke staff role %s is unavailable", STAFF)
        return
    former = guild.get_role(FORMER_STAFF)
    access = discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                         read_message_history=True, attach_files=True)
    async with ticket_lock:
        for channel in guild.text_channels:
            data = ticket_data(channel)
            if not data or data[2] != "open":
                continue
            try:
                current = channel.overwrites_for(staff)
                if any(getattr(current, key) is not True for key, value in access if value is True):
                    current.update(**{key: True for key, value in access if value is True})
                    await channel.set_permissions(staff, overwrite=current, reason="Update Evoke staff role")
                if former and former in channel.overwrites:
                    await channel.set_permissions(former, overwrite=None, reason="Replace former Evoke staff role")
            except discord.HTTPException:
                log.exception("Could not update staff access for ticket %s", channel.id)


@client.event
async def on_ready():
    async with publish_lock:
        await update_open_ticket_access()
        for channel_id in (SUPPORT, REQUIREMENTS):
            try:
                channel = await client.fetch_channel(channel_id)
                if not isinstance(channel, discord.TextChannel) or channel.guild.id != GUILD_ID:
                    log.error("Panel channel %s must be a text channel in guild %s", channel_id, GUILD_ID)
                    continue
                view = public_panel(channel_id)
                title = "# Evoke Support" if channel_id == SUPPORT else "# Join Evoke"
                async for message in channel.history(limit=None):
                    if message.author.id != client.user.id:
                        continue
                    if any(getattr(child, "content", "") == title for component in message.components
                           for child in getattr(component, "children", ())):
                        await message.edit(view=view, attachments=[upload()])
                        break
                else:
                    await channel.send(view=view, file=upload())
                log.info("Panel ready in %s", channel_id)
            except discord.HTTPException:
                log.exception("Could not publish to %s", channel_id)
    log.info("Connected as %s", client.user)


async def health(request):
    return web.Response(text="Evoke Systems online" if client.is_ready() else "Connecting to Discord",
                        status=200 if client.is_ready() else 503)


async def main():
    token = os.getenv("DISCORD_TOKEN") or os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("Set DISCORD_TOKEN in the Render service environment.")
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "10000"))).start()
    try:
        async with client:
            await client.start(token)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
