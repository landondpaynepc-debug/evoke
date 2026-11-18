"""Evoke Systems | Discord support and roster applications."""
import asyncio
import io
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
TRANSCRIPTS = 1558547308054839326
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


REQUESTS = {
    "support": ("General Support", "🎧", ["What do you need help with?", "What have you tried?"]),
    "player": ("Player Applications", "🎮", ["Epic username and tracker link", "Target roster and current PR", "Best recent tournament results"]),
    "creator": ("Creator Applications", "📸", ["Main channel or social profile link", "Follower count and typical views", "What content would you make for Evoke?"]),
    "business": ("Business Inquiries", "💼", ["Name and organization", "What are you proposing?", "Preferred timeline or contact method"]),
    "report": ("Reporting", "❗", ["Who or what are you reporting?", "What happened and when?", "Evidence links (optional)"]),
    "design": ("Designer Application", "🎨", ["Design specialty", "Portfolio or work samples", "Experience and weekly availability"]),
    "staff": ("Staff Application", "🪪", ["Which staff position interests you?", "Describe your relevant experience", "Time zone and availability"]),
    "creative": ("Creative Application", "🎬", ["Creative specialty", "Reel, portfolio, or sample projects", "Pitch a project you would make for Evoke"]),
}


def request_menu():
    options = [discord.SelectOption(label=name, value=key, emoji=emoji)
               for key, (name, emoji, _) in REQUESTS.items()]
    return ui.Select(custom_id=PREFIX + "choose", placeholder="Choose a ticket category", options=options)


def request_form(key):
    name, _, fields = REQUESTS[key]
    modal = ui.Modal(title=name, custom_id=PREFIX + "form:" + key)
    for n, label in enumerate(fields):
        entry = ui.TextInput(custom_id=f"answer-{n}", style=discord.TextStyle.paragraph,
                             max_length=600, required=not label.endswith("(optional)"))
        modal.add_item(ui.Label(text=label, component=entry))
    return modal


def form_answers(i, key):
    fields = REQUESTS[key][2]
    inputs = [row.get("component", {}) for row in i.data.get("components", [])]
    values = {item["custom_id"]: item.get("value", "").strip() for item in inputs if item.get("type") == 4}
    return [(label, values.get(f"answer-{n}", "")) for n, label in enumerate(fields)]


CAREERS = 1566128366040645642
REVIEWERS = 1572743601358635098
REVIEW_LOG = 1572744498729984030
QUESTIONS = [
    "What staff experience do you have?",
    "Why do you want to join Evoke staff?",
    "Why should Evoke trust you as a staff member?",
    "How active are you, and in which time zone?",
    "Two members are arguing. What do you do?",
    "How do you handle a ticket you can’t answer?",
]
application_lock = asyncio.Lock()


def career_form(second=False):
    modal = ui.Modal(title="Trial Staff · Scenarios" if second else "Trial Staff · About you",
                     custom_id=PREFIX + ("career:finish" if second else "career:start"))
    for n in (range(4, 6) if second else range(4)):
        modal.add_item(ui.Label(text=QUESTIONS[n], component=ui.TextInput(
            custom_id=f"q{n}", style=discord.TextStyle.paragraph, max_length=480, required=True)))
    return modal


def career_answers(i, indices):
    fields = {row["component"]["custom_id"]: row["component"].get("value", "").strip()
              for row in i.data.get("components", []) if row.get("component", {}).get("type") == 4}
    answers = []
    for n in indices:
        answer = fields.get(f"q{n}", "")
        if not answer or len(answer) > 480:
            raise ValueError("Please complete each question in the form.")
        answers.append(f"**{n + 1}. {QUESTIONS[n]}**\n{answer}")
    return answers


def application_panel(owner, answers, status, reviewer=0):
    items = ["# Trial Staff application", f"**Applicant** <@{owner}> · **Status** {status}", *answers]
    if status == "Draft":
        items.append(ui.ActionRow(button("Continue application", "career:continue", discord.ButtonStyle.primary)))
    elif status == "Awaiting review":
        items.append(f"<@&{REVIEWERS}> · Use this channel for follow-up questions.")
        items.append(ui.ActionRow(button("Accept", "career:Accepted", discord.ButtonStyle.success),
                                  button("Deny", "career:Denied", discord.ButtonStyle.danger)))
    else:
        items.append(f"**Reviewed by** <@{reviewer}>")
    return panel(*items)


async def career_start(i):
    if i.channel_id not in (CAREERS, SUPPORT):
        return "Open the Careers panel to apply."
    answers = career_answers(i, range(4))
    guild = i.guild
    role = guild.get_role(REVIEWERS)
    if not role:
        return "The reviewer role is unavailable. Please contact Evoke staff."
    prefix = f"evoke-career/{i.user.id}/"
    for ch in await guild.fetch_channels():
        if isinstance(ch, discord.TextChannel) and (ch.topic or "").startswith(prefix):
            state = ch.topic.split("/")[2]
            if state in ("draft", "pending"):
                return f"Continue your application in {ch.mention}."
    allow = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True,
                                       attach_files=True)
    ch = await guild.create_text_channel(f"application-{i.user.id}", category=guild.get_channel(CATEGORY),
        topic=prefix + "draft", overwrites={guild.default_role: discord.PermissionOverwrite(view_channel=False),
                                            guild.me: allow, i.user: allow, role: allow})
    try:
        msg = await ch.send(view=application_panel(i.user.id, answers, "Draft"), file=upload())
        await ch.edit(topic=prefix + f"draft/{msg.id}")
    except discord.HTTPException:
        await ch.delete(reason="Could not initialize application")
        raise
    return f"Your first four answers are saved. Open {ch.mention} and choose **Continue application**."


async def career_action(i, action):
    if action == "start":
        return await career_start(i)
    ch = await i.guild.fetch_channel(i.channel_id)
    parts = (getattr(ch, "topic", "") or "").split("/")
    if len(parts) < 4 or parts[0] != "evoke-career":
        return "Open the application in its private channel."
    owner, state, message_id = int(parts[1]), parts[2], int(parts[3])
    msg = await ch.fetch_message(message_id)
    fields = [x.content for x in msg.components[0].children if isinstance(x, discord.TextDisplay)
              and any(x.content.startswith(f"**{n}. ") for n in range(1, 7))]
    prefix = f"evoke-career/{owner}/"
    if action == "finish":
        if i.user.id != owner or state != "draft":
            return "Only the applicant can finish a draft."
        answers = fields[:4] + career_answers(i, range(4, 6))
        await msg.edit(view=application_panel(owner, answers, "Awaiting review"),
                       allowed_mentions=discord.AllowedMentions.none())
        await ch.edit(topic=prefix + f"pending/{message_id}")
        return "Application submitted. Reviewers can reply in this channel."
    if action not in ("Accepted", "Denied") or not any(r.id == REVIEWERS for r in i.user.roles):
        return "Only the designated Evoke reviewer role can decide applications."
    if i.user.id == owner:
        return "Another reviewer must decide your application."
    if state not in ("pending", "reviewing"):
        return "This application already has a decision or is still a draft."
    if len(fields) != 6:
        return "The application is missing answers. Please contact staff."
    reviewer = i.user.id
    if state == "reviewing":
        action, reviewer = parts[4], int(parts[5])
    else:
        await ch.edit(topic=prefix + f"reviewing/{message_id}/{action}/{reviewer}")
    archive = await i.guild.fetch_channel(REVIEW_LOG)
    if not isinstance(archive, discord.TextChannel) or archive.guild.id != GUILD_ID:
        return "Staff should check the reviewed-applications channel."
    # Recover the existing archive if a previous attempt stopped before finishing.
    marker = f"Application {ch.id}"
    logged = None
    if state == "reviewing":
        async for entry in archive.history(limit=None, after=ch.created_at):
            if entry.author.id == client.user.id and marker in list(t for cmp in entry.components for t in component_text(cmp)):
                logged = entry
                break
    if logged is None:
        record = application_panel(owner, fields, action, reviewer)
        record.children[0].add_item(ui.TextDisplay(marker))
        logged = await archive.send(view=record, file=upload())
    # Commit the outcome before optional notification so competing clicks cannot change it.
    await ch.edit(topic=prefix + f"{action}/{message_id}/{logged.id}")
    notice = f"Your Trial Staff application to **Evoke Esports** was **{action.lower()}**."
    try:
        user = await client.fetch_user(owner)
        await user.send(view=panel("# Evoke Careers", notice, f"Application channel: {ch.mention}"), file=upload())
        delivery = "DM delivered."
    except discord.HTTPException:
        delivery = "DM could not be delivered. The applicant can see the decision here."
    final = application_panel(owner, fields, action, reviewer)
    final.children[0].add_item(ui.TextDisplay(delivery))
    await msg.edit(view=final)
    return f"Application {action.lower()} and archived. {delivery}"


def public_panel(channel):
    if channel == CAREERS:
        return panel("# Evoke Careers", "## Join the Trial Staff team\nHelp members get answers in tickets and keep the Discord welcoming.",
                     "**What we look for**\n• Active in the Discord\n• Professional and respectful\n• Willing to answer tickets and help members\n• Responsible and trustworthy",
                     "Anyone can apply. Answer four questions about yourself, then two scenarios in your private application channel.",
                     ui.ActionRow(button("Apply for Trial Staff", "career:apply", discord.ButtonStyle.primary)))
    if channel == SUPPORT:
        return panel("# Evoke Support", "Choose a category below and fill out its form. Your answers go into a private ticket with Evoke staff.",
                     ui.ActionRow(request_menu()))
    return panel("# Join Evoke", "## Competitive roster\nInclude **evk** in your username. "
                 "Find your division by Power Ranking Points (PR).",
                 "**Team Evoke**  ·  30,000+ PR\n**Evoke Competitive**  ·  10,000+ PR\n"
                 "**Evoke Semi Pro**  ·  5,000+ PR\n**Academy**  ·  3,000+ PR and consistently high event finishes\n"
                 "**Grinders**  ·  500–2,999+ PR\n**Future**  ·  0–500 PR",
                 ui.Separator(), "## Content roster\n**Main Content**  ·  10,000 followers or subscribers\n"
                 "Include **evk** on your social profiles.",
                 ui.Separator(), f"## How to apply\nGo to <#{SUPPORT}> and choose **Player Applications** or **Creator Applications**. "
                 "Submit the form to open your private application ticket.")


def ticket_panel(owner, role, key, answers):
    details = [f"**{label}**\n{discord.utils.escape_markdown(answer) or '—'}" for label, answer in answers]
    name = REQUESTS[key][0] if key else "Evoke Support"
    return panel(f"# {name}", f"{role.mention} · New ticket",
                 f"**Opened by** <@{owner}>\n**Status** Waiting for staff",
                 *details, ui.ActionRow(button("Claim ticket", "claim"),
                                        button("Close ticket", "close", discord.ButtonStyle.danger)))


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


async def open_ticket(i, key=None):
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
        answers = form_answers(i, key) if key else []
        if any(not answer and not label.endswith("(optional)") for label, answer in answers):
            return "Please reopen the form and fill out each required field."
        allow = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True,
                                           attach_files=True)
        overwrites = {i.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                      i.user: allow, role: allow, i.guild.me: allow}
        channel = await i.guild.create_text_channel(f"{key or 'support'}-{i.user.id}", category=category,
                                                    overwrites=overwrites, topic=topic(i.user.id))
        try:
            await channel.send(view=ticket_panel(i.user.id, role, key, answers), file=upload(),
                               allowed_mentions=discord.AllowedMentions(roles=[role], users=False, everyone=False))
        except discord.HTTPException:
            await channel.delete(reason="Ticket panel failed to publish")
            raise
        return f"Your ticket is ready in {channel.mention}."


def component_text(component):
    text = getattr(component, "content", None)
    if text:
        yield text
    for child in getattr(component, "children", []):
        yield from component_text(child)


async def save_transcript(channel, reason):
    destination = await channel.guild.fetch_channel(TRANSCRIPTS)
    if not isinstance(destination, discord.TextChannel) or destination.id == channel.id:
        raise RuntimeError("Transcript destination must be a separate text channel.")
    if not channel.permissions_for(channel.guild.me).read_message_history:
        raise RuntimeError("Missing Read Message History in the ticket.")
    body = io.BytesIO()
    body.write(f"Evoke Support | #{channel.name} ({channel.id})\n{channel.topic}\n{reason}\n\n".encode())
    count = 0
    async for msg in channel.history(limit=None, oldest_first=True):
        lines = [f"[{msg.created_at.isoformat()}] {msg.author} ({msg.author.id})", msg.content]
        for component in msg.components:
            lines.extend(component_text(component))
        for embed in msg.embeds:
            lines.extend(filter(None, [embed.title, embed.description]))
            lines.extend(f"{f.name}: {f.value}" for f in embed.fields)
        lines.extend(f"Attachment: {a.filename} {a.url}" for a in msg.attachments)
        lines.extend(f"Sticker: {s.name}" for s in msg.stickers)
        if not any(lines[1:]):
            if msg.type in (discord.MessageType.default, discord.MessageType.reply) and not msg.author.bot:
                raise RuntimeError("Discord withheld message content. Enable Message Content Intent for Evoke Systems in the Developer Portal.")
            lines.append(f"[{msg.type.name}]")
        body.write(("\n".join(line for line in lines if line) + "\n\n").encode("utf-8"))
        count += 1
    body.seek(0)
    size = min(channel.guild.filesize_limit, 8 * 1024 * 1024)
    part = 0
    while body.tell() < body.getbuffer().nbytes:
        part += 1
        chunk = body.read(size)
        # Keep every part valid UTF-8.
        if body.tell() < body.getbuffer().nbytes:
            valid = chunk.decode("utf-8", errors="ignore").encode()
            body.seek(len(valid) - len(chunk), 1)
            chunk = valid
        await destination.send(f"Ticket transcript • #{channel.name} • {count} messages • part {part}",
                               file=discord.File(io.BytesIO(chunk), filename=f"ticket-{channel.id}-{part}.txt"))


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
            view = ui.LayoutView.from_message(i.message)
            for item in view.walk_children():
                if isinstance(item, ui.TextDisplay) and item.content.startswith(f"**Opened by** <@{owner}>\n"):
                    item.content = f"**Opened by** <@{owner}>\n**Status** Claimed by <@{i.user.id}>"
                    break
            await i.message.edit(view=view, allowed_mentions=discord.AllowedMentions.none())
            return "You claimed this ticket."
        if action in ("close", "confirm"):  # Older confirmations promised an archive.
            await i.edit_original_response(view=confirmation(), attachments=[upload()])
            return None
        if action == "delete":
            await i.edit_original_response(content="Saving the transcript before deleting this ticket…")
            await save_transcript(channel, f"Closed by {i.user.id}")
            await channel.delete(reason=f"Evoke ticket closed by {i.user.id}")
            return None


def confirmation():
    return panel("## Close ticket?", "Confirming saves a transcript for staff and permanently deletes this channel.", ui.ActionRow(button("Delete ticket", "delete", discord.ButtonStyle.danger)))


@client.event
async def on_interaction(i):
    cid = (i.data or {}).get("custom_id", "")
    if i.guild_id != GUILD_ID or not cid.startswith(PREFIX):
        return
    if cid == PREFIX + "career:apply" and i.channel_id == CAREERS:
        await i.response.send_modal(career_form())
        return
    if cid == PREFIX + "career:continue":
        if not (getattr(i.channel, "topic", "") or "").startswith(f"evoke-career/{i.user.id}/draft/"):
            await i.response.send_message("Only the applicant can continue a draft.", ephemeral=True)
            return
        await i.response.send_modal(career_form(second=True))
        return
    if cid == PREFIX + "choose" and i.channel_id == SUPPORT:
        choices = i.data.get("values", [])
        if choices and choices[0] in REQUESTS:
            await i.response.send_modal(career_form() if choices[0] == 'staff' else request_form(choices[0]))
            # Re-render the shared menu with no default selection.
            await i.message.edit(view=public_panel(SUPPORT))
        return
    await i.response.defer(ephemeral=True, thinking=True)
    try:
        action = cid[len(PREFIX):]
        if action.startswith("career:"):
            async with application_lock:
                result = await career_action(i, action.split(":", 1)[1])
        elif action.startswith("form:") and action[5:] in REQUESTS and i.type == discord.InteractionType.modal_submit:
            result = await open_ticket(i, action[5:])
        elif action == "open":
            result = await open_ticket(i)
        else:
            result = await manage_ticket(i, action)
        if result:
            await i.followup.send(result, ephemeral=True)
    except ValueError as error:
        await i.followup.send(str(error), ephemeral=True)
    except RuntimeError as error:
        log.exception("Ticket action could not finish in %s", i.channel_id)
        await i.followup.send(f"The ticket remains open. {error}", ephemeral=True)
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
        for channel_id in (SUPPORT, REQUIREMENTS, CAREERS):
            try:
                channel = await client.fetch_channel(channel_id)
                if not isinstance(channel, discord.TextChannel) or channel.guild.id != GUILD_ID:
                    log.error("Panel channel %s must be a text channel in guild %s", channel_id, GUILD_ID)
                    continue
                view = public_panel(channel_id)
                title = {SUPPORT: "# Evoke Support", REQUIREMENTS: "# Join Evoke", CAREERS: "# Evoke Careers"}[channel_id]
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
