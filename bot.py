"""Evoke Systems | Discord support and roster applications."""
import asyncio
import hashlib
from datetime import timedelta
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
from discord.ext import tasks
from moderation import handle_command
import activity_stats
import partners
import voice_rooms

GUILD_ID = 1454719356226240517
CATEGORY = 1558547311590903831
TRANSCRIPTS = 1558547308054839326
STAFF = 1565166580411924610
FORMER_STAFF = 1560752530366144543
WELCOME = 1573081919648694352
JOIN_ROLE = 1546383491665756311
WELCOME_EMOJI = "<a:pfp:1565463462388441138>"
SUPPORT = 1566118218605662409
REQUIREMENTS = 1566120622294048819
BANNER = ROOT / "banner.webp"
TICKET_BUTTONS = "evoke-ticket:v2:"
PREFIX = "evoke:v1:"
log = logging.getLogger("evoke")
ticket_lock = asyncio.Lock()
publish_lock = asyncio.Lock()
client: discord.Client


def button(label, action, style=discord.ButtonStyle.secondary):
    prefix = TICKET_BUTTONS if action in ('close', 'delete', 'delete-partial') else PREFIX
    return ui.Button(label=label, custom_id=prefix + action, style=style)


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
REVIEWERS = 1574906941745008800
ACCEPTANCE_ROLES = (1571298522685247598, STAFF)
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


def acceptance_role_error(guild, roles):
    if not guild.me.guild_permissions.manage_roles:
        return "Give Evoke Systems the Manage Roles permission."
    for role in roles:
        if role is None:
            return "An acceptance role is missing from the server."
        if not role.is_assignable():
            return f"Check that Evoke Systems is above the assignable role {role.mention} in Server Settings."
    return None


async def grant_accepted_roles(guild, owner, reviewer):
    try:
        member = await guild.fetch_member(owner)
    except discord.NotFound:
        return "The applicant is no longer in this server."
    current = {role.id for role in member.roles}
    missing = [guild.get_role(role_id) for role_id in ACCEPTANCE_ROLES if role_id not in current]
    if missing:
        error = acceptance_role_error(guild, missing)
        if error:
            return error
        try:
            await member.add_roles(*missing, reason=f"Evoke Careers acceptance by {reviewer}", atomic=True)
        except discord.Forbidden:
            return "Discord blocked the grant. Check Manage Roles, the role order, and whether Evoke Systems can manage this member."
    return None


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
    if action == "Accepted":
        error = await grant_accepted_roles(i.guild, owner, reviewer)
        if error:
            return f"Acceptance is waiting for the roles. {error} Click Accept again to retry."
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
    if action == "Accepted":
        notice += " Your two staff roles have been assigned."
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


# Application state lives in the bot's DM and review messages so drafts survive restarts.
dm_review_lock = asyncio.Lock()


def dm_buttons(message):
    return [item.custom_id for top in message.components for row in getattr(top, "children", [])
            for item in getattr(row, "children", []) if getattr(item, "custom_id", None)]


def saved_answers(message):
    return [item.content for item in message.components[0].children if isinstance(item, discord.TextDisplay)
            and any(item.content.startswith(f"**{n}. ") for n in range(1, 7))]


async def send_dm_application(i):
    dm = await i.user.create_dm()
    async with dm_review_lock:
        async for msg in dm.history(limit=None):
            ids = dm_buttons(msg) if msg.author.id == client.user.id else []
            if any(x in (PREFIX + "applydm:first", PREFIX + "applydm:second") for x in ids) or (ids and any(
                t == "# Trial Staff application submitted" for cmp in msg.components for t in component_text(cmp)
            )):
                return f"Your application is already in your DMs. [Open application]({msg.jump_url})"
            if msg.author.id == client.user.id and any(
                t == "# Trial Staff decision" for cmp in msg.components for t in component_text(cmp)
            ):
                break
        msg = await dm.send(view=panel("# Evoke Careers", "## Apply for Trial Staff",
                            "Start with four questions about yourself. Then answer two staff scenarios and submit.",
                            ui.ActionRow(button("Start application", "applydm:first", discord.ButtonStyle.primary))), file=upload())
    return f"I sent your application by DM. [Open application]({msg.jump_url})"


def review_view(owner, draft, answers, state="Pending", reviewer=None):
    suffix = f":{owner}:{draft}"
    items = ["# Trial Staff application", f"**Applicant** <@{owner}> · **Status** {state}",
             f"Application {draft}", *answers]
    actions = []
    if state == "Pending":
        actions.extend([button("Accept", "reviewdm:Accepted" + suffix, discord.ButtonStyle.success),
                        button("Deny", "reviewdm:Denied" + suffix, discord.ButtonStyle.danger)])
    elif state == "Assigning roles":
        actions.append(button("Retry acceptance", f"reviewdm:retry{suffix}:{reviewer}"))
    if reviewer:
        items.append(f"**Reviewed by** <@{reviewer}>")
    actions.append(button("Message applicant", "reviewdm:contact" + suffix))
    return panel(*items, ui.ActionRow(*actions))


def chat_form(action):
    modal = ui.Modal(title="Application message", custom_id=PREFIX + action)
    modal.add_item(ui.Label(text="Your message", component=ui.TextInput(custom_id="body",
                           style=discord.TextStyle.paragraph, required=True, max_length=1500)))
    return modal


async def dm_application_action(i, action):
    parts = action.split(":")
    verb = parts[1]
    if verb in ("first", "second"):
        modal = career_form(second=verb == "second")
        modal.custom_id = PREFIX + f"applydm:save-{verb}:{i.message.id}"
        await i.response.send_modal(modal)
        return
    if verb == "reply":
        await i.response.send_modal(chat_form(f"applydm:send-reply:{parts[2]}"))
        return
    await i.response.defer(thinking=True)
    async with dm_review_lock:
        guild = client.get_guild(GUILD_ID)
        if verb == "send-reply":
            queue = await guild.fetch_channel(REVIEW_LOG)
            review = await queue.fetch_message(int(parts[2]))
            if not any(x.startswith(PREFIX + f"reviewdm:contact:{i.user.id}:") for x in dm_buttons(review)):
                raise ValueError("This application belongs to another member.")
            body = i.data["components"][0]["component"]["value"]
            await review.reply(view=panel("## Applicant reply", f"<@{i.user.id}>", body), file=upload(), mention_author=False)
            result = "Your reply was sent to the reviewers."
        else:
            msg = await i.channel.fetch_message(int(parts[2]))
            stage = {"save-first": "first", "save-second": "second"}.get(verb)
            if not stage or msg.author.id != client.user.id or PREFIX + "applydm:" + stage not in dm_buttons(msg):
                raise ValueError("This step has already been completed.")
            if stage == "first":
                answers = career_answers(i, range(4))
                await msg.edit(view=panel("# Evoke Careers", *answers,
                    ui.ActionRow(button("Answer scenarios & submit", "applydm:second", discord.ButtonStyle.primary))))
                result = f"Your first four answers are saved. [Continue with the two scenarios]({msg.jump_url})."
            else:
                answers = saved_answers(msg) + career_answers(i, range(4, 6))
                if len(answers) != 6:
                    raise ValueError("Your draft is missing answers. Please contact Evoke staff.")
                queue = await guild.fetch_channel(REVIEW_LOG)
                existing = None
                async for entry in queue.history(limit=None, after=msg.created_at):
                    if entry.author.id == client.user.id and PREFIX + f"reviewdm:contact:{i.user.id}:{msg.id}" in dm_buttons(entry):
                        existing = entry
                        break
                review = existing or await queue.send(view=review_view(i.user.id, msg.id, answers), file=upload())
                await msg.edit(view=panel("# Trial Staff application submitted", *answers,
                    "The Evoke reviewers have your application. Your decision will arrive here.",
                    ui.ActionRow(button("Message reviewers", f"applydm:reply:{review.id}"))))
                result = "Your application has been submitted to Evoke staff."
    await i.edit_original_response(content=result)


async def review_dm_action(i, action):
    if i.channel_id != REVIEW_LOG or not any(role.id == REVIEWERS for role in i.user.roles):
        await i.response.send_message("Only Evoke application reviewers can do that.", ephemeral=True)
        return
    _, verb, owner_id, draft_id, *extra = action.split(":")
    owner, draft = int(owner_id), int(draft_id)
    if verb == "contact":
        await i.response.send_modal(chat_form(f"reviewdm:send:{owner}:{draft}:{i.message.id}"))
        return
    await i.response.defer(thinking=True, ephemeral=True)
    async with dm_review_lock:
        if verb == "send":
            message = await i.channel.fetch_message(int(extra[0]))
            if PREFIX + f"reviewdm:contact:{owner}:{draft}" not in dm_buttons(message):
                raise ValueError("The application could not be verified.")
            body = i.data["components"][0]["component"]["value"]
            user = await client.fetch_user(owner)
            await user.send(view=panel("# Message from Evoke Careers", body,
                ui.ActionRow(button("Reply to reviewers", f"applydm:reply:{message.id}"))), file=upload())
            result = "Message delivered."
        else:
            message = await i.channel.fetch_message(i.message.id)
            if PREFIX + action not in dm_buttons(message) or verb not in ("Accepted", "Denied", "retry"):
                raise ValueError("This application already has a decision.")
            if i.user.id == owner:
                raise ValueError("Another reviewer must decide your application.")
            answers = saved_answers(message)
            reviewer = int(extra[0]) if verb == "retry" else i.user.id
            outcome = "Accepted" if verb == "retry" else verb
            if outcome == "Accepted":
                if verb != "retry":
                    await message.edit(view=review_view(owner, draft, answers, "Assigning roles", reviewer))
                error = await grant_accepted_roles(i.guild, owner, reviewer)
                if error:
                    raise ValueError(error + " Use Retry acceptance when ready.")
            await message.edit(view=review_view(owner, draft, answers, outcome, reviewer))
            notice = f"Your Trial Staff application to **Evoke Esports** was **{outcome.lower()}**."
            if outcome == "Accepted":
                notice += " Your two staff roles have been assigned."
            try:
                user = await client.fetch_user(owner)
                dm = await user.create_dm()
                await dm.send(view=panel("# Trial Staff decision", notice), file=upload())
                result = f"{outcome}. Decision delivered by DM."
            except discord.HTTPException:
                result = f"{outcome}. Discord could not deliver the applicant’s DM."
            final = review_view(owner, draft, answers, outcome, reviewer)
            final.children[0].add_item(ui.TextDisplay(result))
            await message.edit(view=final)
    await i.edit_original_response(content=result)


SOCIALS = 1556210205606289418
GUIDELINES = 1549820804064219227
PANEL_TITLES = {SUPPORT: "# Evoke Support", REQUIREMENTS: "# Join Evoke", CAREERS: "# Evoke Careers",
                SOCIALS: "# Evoke Socials", GUIDELINES: "# Evoke Guidelines"}


def public_panel(channel):
    if channel == SOCIALS:
        return panel(PANEL_TITLES[SOCIALS],
                     "Find Evoke’s competitive profile, follow the team, or join the Discord.",
                     ui.ActionRow(ui.Button(label="Fortnite Tracker", url="https://fortnitetracker.com/esports/organization/evoke-esports"),
                                  ui.Button(label="X", url="https://x.com/EvokeEsport"),
                                  ui.Button(label="YouTube", url="https://www.youtube.com/@Evokeesportsofficial")),
                     ui.ActionRow(ui.Button(label="TikTok", url="https://www.tiktok.com/@officalevoke"),
                                  ui.Button(label="Discord", url="https://discord.gg/evokeesports")))
    if channel == GUIDELINES:
        return panel(PANEL_TITLES[GUIDELINES],
                     "These rules apply in Discord, in competition, and wherever you represent Evoke.",
                     ui.Separator(),
                     "### 1 · Respect & conduct\nTreat members, staff, players, and partners with respect. "
                     "Harassment, bullying, discrimination, and stirring up drama are prohibited. "
                     "Keep conversations mature and appropriate. English is our primary language.",
                     "### 2 · Competitive integrity\nPlay fairly in every tournament, scrim, and event. "
                     "Cheating, exploiting bugs, illegal teaming, account sharing, boosting, and other unfair advantages are prohibited. Follow each event’s rules.",
                     "### 3 · Content & promotion\nKeep posts, jokes, and memes appropriate. "
                     "Keep NSFW, illegal, and excessively graphic content out of the server. Avoid spam, mass tagging, and unsolicited ads. "
                     "Get staff permission before promoting another organization or server.",
                     "### 4 · Voice & text channels\nUse each channel for its intended purpose. "
                     "Keep voice chat clear of yelling, disruptive soundboards, and deliberate interruptions.",
                     ui.Separator(),
                     "### 5 · Privacy & security\nKeep personal information private, including your own. "
                     "Doxxing, threats, scams, and attempts to compromise another member’s account are prohibited. Report suspicious activity to staff immediately.",
                     "### 6 · Moderation\nFollow directions from staff and team leaders, and respect warnings and moderation decisions. "
                     "Violations can lead to a warning, loss of roles, or removal from the server. Serious or repeated violations may result in a permanent ban.",
                     "### 7 · Represent Evoke\nSupport your teammates, help others improve, and show good sportsmanship wherever you play. Have fun competing together.",
                     ui.Separator(), f"**Need to report a concern?** Open a private ticket in <#{SUPPORT}> and choose **Reporting**. You can report concerns about members or staff.")
    if channel == CAREERS:
        return panel("# Evoke Careers", "## Join the Trial Staff team\nHelp members get answers in tickets and keep the Discord welcoming.",
                     "**What we look for**\n• Active in the Discord\n• Professional and respectful\n• Willing to answer tickets and help members\n• Responsible and trustworthy",
                     "Anyone can apply. Evoke Systems will DM you four questions about yourself and two staff scenarios.",
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


async def save_transcript(channel, reason, allow_partial=False):
    destination = await channel.guild.fetch_channel(TRANSCRIPTS)
    if not isinstance(destination, discord.TextChannel) or destination.id == channel.id:
        raise RuntimeError("Transcript destination must be a separate text channel.")
    if not channel.permissions_for(channel.guild.me).read_message_history:
        raise RuntimeError("Missing Read Message History in the ticket.")
    body = io.BytesIO()
    body.write(f"Evoke Support | #{channel.name} ({channel.id})\n{channel.topic}\n{reason}\n\n".encode())
    count = 0
    unavailable = 0
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
                unavailable += 1
                lines.append(f"[Message {msg.id}: content unavailable]")
            else:
                lines.append(f"[{msg.type.name}]")
        body.write(("\n".join(line for line in lines if line) + "\n\n").encode("utf-8"))
        count += 1
    if unavailable and not allow_partial:
        return unavailable
    if unavailable:
        body.write(f"\nPartial transcript: {unavailable} entries have unavailable content.\n".encode())
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
        await destination.send(f"{'Partial ticket transcript' if unavailable else 'Ticket transcript'} • #{channel.name} • {count} messages • part {part}",
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
        if action in ("delete", "delete-partial"):
            await i.edit_original_response(content="Saving the transcript before deleting this ticket…")
            missing = await save_transcript(channel, f"Closed by {i.user.id}", allow_partial=action == "delete-partial")
            if missing:
                warning = panel("## Some message content is unavailable",
                                f"Discord returned {missing} entries without readable content. You can leave the ticket open or save the available messages, authors, and timestamps before deleting it.",
                                ui.ActionRow(button("Save partial transcript & close", "delete-partial", discord.ButtonStyle.danger)))
                await i.edit_original_response(content=None, view=warning, attachments=[upload()])
                return None
            await channel.delete(reason=f"Evoke ticket closed by {i.user.id}")
            return None


def confirmation():
    return panel("## Close ticket?", "Confirming saves a transcript for staff and permanently deletes this channel.", ui.ActionRow(button("Delete ticket", "delete", discord.ButtonStyle.danger)))


async def on_interaction(i):
    cid = (i.data or {}).get("custom_id", "")
    if i.guild_id == GUILD_ID and cid.startswith("partner:"):
        await partners.handle(i, STAFF, panel)
        return
    if i.guild_id == GUILD_ID and cid.startswith("evc:"):
        await voice_rooms.handle(i)
        return
    if i.guild_id == GUILD_ID and cid.startswith("evstats:"):
        await activity_stats.interaction(i)
        return
    if cid.startswith(TICKET_BUTTONS):
        if i.guild_id != GUILD_ID:
            return
        action = cid[len(TICKET_BUTTONS):]
        if action == "close":
            data = ticket_data(i.channel)
            authorized = data and (i.user.id == data[0] or any(r.id == STAFF for r in i.user.roles))
            if not authorized:
                await i.response.send_message("Only the opener or Evoke staff can close this ticket.", ephemeral=True)
                return
            await i.response.send_message(view=confirmation(), file=upload(), ephemeral=True)
            return
        if action in ("delete", "delete-partial"):
            await i.response.send_message("Preparing your ticket transcript…", ephemeral=True)
            try:
                result = await manage_ticket(i, action)
                if result:
                    await i.edit_original_response(content=result)
            except (RuntimeError, discord.HTTPException):
                log.exception("Ticket closure failed in %s", i.channel_id)
                await i.followup.send("Closure could not finish. Check transcript and channel permissions.", ephemeral=True)
            return
        return
    if not cid.startswith(PREFIX):
        return
    action = cid[len(PREFIX):]
    staff_menu = (action == "choose" and i.channel_id == SUPPORT and i.data.get("values") == ["staff"])
    start_dm = i.guild_id == GUILD_ID and ((action == "career:apply" and i.channel_id == CAREERS) or staff_menu)
    in_dm = i.guild_id is None and action.startswith("applydm:")
    in_review = i.guild_id == GUILD_ID and action.startswith("reviewdm:")
    if start_dm or in_dm or in_review:
        try:
            if start_dm:
                await i.response.defer(ephemeral=True, thinking=True)
                result = await send_dm_application(i)
                await i.edit_original_response(content=result)
                if staff_menu:
                    await i.message.edit(view=public_panel(SUPPORT))
            elif in_dm:
                await dm_application_action(i, action)
            else:
                await review_dm_action(i, action)
        except (ValueError, discord.HTTPException) as error:
            log.warning("Careers action %s failed: %s", action, type(error).__name__)
            feedback = str(error) if isinstance(error, ValueError) else "Discord could not complete this action. Check DM privacy and the bot’s channel permissions, then retry."
            if i.response.is_done():
                await i.followup.send(feedback, ephemeral=bool(i.guild_id))
            else:
                await i.response.send_message(feedback, ephemeral=bool(i.guild_id))
        return
    if i.guild_id != GUILD_ID:
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
            await i.response.send_modal(request_form(choices[0]))
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
                async for message in channel.history(limit=None, oldest_first=True):
                    if message.author.id != client.user.id:
                        continue
                    ids = dm_buttons(message)
                    if TICKET_BUTTONS + "close" in ids:
                        break
                    if PREFIX + "close" in ids:
                        view = ui.LayoutView.from_message(message)
                        for item in view.walk_children():
                            if getattr(item, "custom_id", None) == PREFIX + "close":
                                item.custom_id = TICKET_BUTTONS + "close"
                        await message.edit(view=view, allowed_mentions=discord.AllowedMentions.none())
                        log.info("Updated close control in ticket %s", channel.id)
                        break
                current = channel.overwrites_for(staff)
                if any(getattr(current, key) is not True for key, value in access if value is True):
                    current.update(**{key: True for key, value in access if value is True})
                    await channel.set_permissions(staff, overwrite=current, reason="Update Evoke staff role")
                if former and former in channel.overwrites:
                    await channel.set_permissions(former, overwrite=None, reason="Replace former Evoke staff role")
            except discord.HTTPException:
                log.exception("Could not update staff access for ticket %s", channel.id)


@tasks.loop(seconds=30)
async def rotate_presence():
    await client.wait_until_ready()
    guild = client.get_guild(GUILD_ID)
    if guild is None or guild.unavailable:
        return
    if rotate_presence.current_loop % 2 == 0:
        count = guild.member_count
        if count is None or not client.intents.members:
            try:
                snapshot = await client.fetch_guild(GUILD_ID, with_counts=True)
                count = snapshot.approximate_member_count
            except discord.HTTPException:
                log.warning("Could not refresh the Evoke member count")
        if count is None:
            return
        activity = discord.Activity(type=discord.ActivityType.watching,
                                    name=f"over {count:,} {'member' if count == 1 else 'members'}")
    else:
        activity = discord.CustomActivity(name="discord.gg/evokeesports")
    await client.change_presence(activity=activity)
    if rotate_presence.current_loop < 2:
        log.info("Presence sent: %s", activity.name)


@tasks.loop(seconds=30)
async def collect_activity():
    await client.wait_until_ready()
    guild = client.get_guild(GUILD_ID)
    if not guild or guild.unavailable:
        activity_stats.voice_reset()
        return
    try:
        await activity_stats.initialize(guild)
        activity_stats.sync_voice(guild)
        activity_stats.prune(discord.utils.utcnow().date())
        await activity_stats.checkpoint()
    except (discord.HTTPException, RuntimeError):
        log.exception("Activity collection could not complete")


async def on_voice_state_update(member, before, after):
    if member.guild.id == GUILD_ID:
        activity_stats.voice_event(member, before, after)
        await voice_rooms.voice_event(member, before, after)


async def on_disconnect():
    activity_stats.voice_reset()


async def on_ready():
    if not collect_activity.is_running():
        collect_activity.start()
    if not rotate_presence.is_running():
        rotate_presence.start()
    async with publish_lock:
        guild = client.get_guild(GUILD_ID)
        if guild and guild.me:
            join_role = guild.get_role(JOIN_ROLE)
            log.info("Join role %s assignable=%s", JOIN_ROLE,
                     bool(join_role and guild.me.guild_permissions.manage_roles and join_role.is_assignable()))
            target = guild.get_channel(WELCOME)
            log.info("Welcome configuration: member_events=%s send=%s fallback_join_messages=%s",
                     client.intents.members, bool(target and target.permissions_for(guild.me).send_messages),
                     bool(guild.system_channel and guild.system_channel_flags.join_notifications and guild.system_channel.permissions_for(guild.me).view_channel))
            error = acceptance_role_error(guild, [guild.get_role(r) for r in ACCEPTANCE_ROLES])
            if error:
                log.warning("Careers role configuration: %s", error)
            else:
                log.info("Careers acceptance roles are assignable")
        if guild:
            try:
                await activity_stats.initialize(guild)
                await voice_rooms.setup(client, guild, activity_stats.storage, upload)
            except (discord.HTTPException, RuntimeError):
                log.exception("VC interface setup failed")
            try:
                await activity_stats.initialize(guild)
                count = await partners.setup(guild, activity_stats.storage, panel, upload)
                log.info("Partner panel ready in %s with %s partners", partners.CHANNEL, count)
            except (discord.HTTPException, ValueError, RuntimeError):
                log.exception("Partner directory setup failed")
            await clean_recent_welcomes(guild)
        await update_open_ticket_access()
        for channel_id, title in PANEL_TITLES.items():
            try:
                channel = await client.fetch_channel(channel_id)
                if not isinstance(channel, discord.TextChannel) or channel.guild.id != GUILD_ID:
                    log.error("Panel channel %s must be a text channel in guild %s", channel_id, GUILD_ID)
                    continue
                view = public_panel(channel_id)
                if channel_id == SUPPORT:
                    existing = [m async for m in channel.history(limit=None)
                                if m.author.id == client.user.id and any(
                                    getattr(child, "content", "") == title for component in m.components
                                    for child in getattr(component, "children", ()))]
                    # Repost once for the November 24 request, then reuse the replacement.
                    if existing and existing[0].created_at.timestamp() >= 1795559020:
                        current = existing.pop(0)
                        await current.edit(view=view, attachments=[upload()])
                    else:
                        current = await channel.send(view=view, file=upload())
                    for previous in existing:
                        try:
                            await previous.delete()
                        except discord.NotFound:
                            pass
                    log.info("Support panel ready: %s", current.jump_url)
                    continue
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


def welcome_panel(member, count):
    view = ui.LayoutView(timeout=None)
    count_label = f"Members: {count:,}" if count is not None else "Members"
    view.add_item(ui.Container(
        ui.TextDisplay(f"{WELCOME_EMOJI} Welcome {member.mention} to **Evoke Esports™**! "
                       "Check out the dashboard to get support or apply for a roster."),
        ui.ActionRow(ui.Button(label=count_label, disabled=True, custom_id="evoke:member-count"),
                     ui.Button(label="Dashboard", emoji="📋", url=f"https://discord.com/channels/{GUILD_ID}/{SUPPORT}"))))
    return view


async def greet(member, count):
    channel = member.guild.get_channel(WELCOME)
    if not isinstance(channel, discord.TextChannel):
        log.error("Welcome channel %s unavailable", WELCOME)
        return
    joined = member.joined_at.isoformat() if member.joined_at else "recent"
    nonce = hashlib.blake2s(f"welcome/{member.guild.id}/{member.id}/{joined}".encode(), digest_size=12).hexdigest()
    await channel.send(view=welcome_panel(member, count), nonce=nonce,
                       allowed_mentions=discord.AllowedMentions(users=[member], roles=False, everyone=False))


async def clean_recent_welcomes(guild):
    channel = guild.get_channel(WELCOME)
    if not isinstance(channel, discord.TextChannel):
        return
    cutoff = discord.utils.utcnow() - timedelta(hours=1)
    seen_times = {}
    members = {}
    removed = 0
    try:
        async for msg in channel.history(limit=None, after=cutoff, oldest_first=True):
            if msg.author.id != client.user.id:
                continue
            lines = [line for cmp in msg.components for line in component_text(cmp)]
            prefix = f"{WELCOME_EMOJI} Welcome <@"
            line = next((s for s in lines if s.startswith(prefix)), "")
            raw_id, sep, tail = line[len(prefix):].partition("> to **Evoke Esports™**!")
            if not sep or not raw_id.isdecimal():
                continue
            uid = int(raw_id)
            if uid in seen_times and uid not in members:
                try:
                    members[uid] = await guild.fetch_member(uid)
                except discord.NotFound:
                    members[uid] = None
            if uid in seen_times:
                member = members[uid]
                if member and member.joined_at and seen_times[uid] >= member.joined_at:
                    await msg.delete()
                    removed += 1
                    continue
            seen_times[uid] = msg.created_at
        log.info("Removed %d duplicate welcome messages", removed)
    except discord.HTTPException:
        log.exception("Could not check recent welcome messages")


async def assign_join_role(member):
    guild = member.guild
    role = guild.get_role(JOIN_ROLE)
    if role is None:
        log.error("Join role %s is missing", JOIN_ROLE)
        return
    if role in member.roles:
        return
    try:
        await member.add_roles(role, reason="Evoke member joined")
    except discord.HTTPException:
        log.exception("Could not assign join role %s to member %s", JOIN_ROLE, member.id)


async def on_member_join(member):
    if member.guild.id == GUILD_ID:
        await assign_join_role(member)
        await greet(member, member.guild.member_count)


async def on_message(message):
    if message.guild and message.guild.id == GUILD_ID and not message.author.bot:
        await activity_stats.message_event(message)
        if message.content.strip().lower() == ".addpartner":
            await partners.command(message, STAFF)
        else:
            await handle_command(message)
    if (not client.intents.members and message.guild and message.guild.id == GUILD_ID
            and message.type == discord.MessageType.new_member):
        member = message.author if isinstance(message.author, discord.Member) else await message.guild.fetch_member(message.author.id)
        await assign_join_role(member)
        snapshot = await client.fetch_guild(GUILD_ID, with_counts=True)
        await greet(member, snapshot.approximate_member_count)


async def health(request):
    return web.Response(text="Evoke Systems online" if client.is_ready() else "Connecting to Discord",
                        status=200 if client.is_ready() else 503)


async def main():
    global client
    token = os.getenv("DISCORD_TOKEN") or os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("Set DISCORD_TOKEN in the Render service environment.")
    async with discord.Client(intents=discord.Intents.none()) as probe:
        await probe.login(token)
        flags = probe.application_flags
        member_events = flags.gateway_guild_members or flags.gateway_guild_members_limited
        content_access = flags.gateway_message_content or flags.gateway_message_content_limited
    log.info("Discord message content access: %s", content_access)
    intents = discord.Intents(guilds=True, members=member_events, guild_messages=True, voice_states=True,
                             message_content=content_access)
    client = discord.Client(intents=intents, allowed_mentions=discord.AllowedMentions.none(),
                            chunk_guilds_at_startup=False)
    for event in (on_interaction, on_ready, on_member_join, on_message, on_voice_state_update, on_disconnect):
        client.event(event)
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
        collect_activity.cancel()
        rotate_presence.cancel()
        await runner.cleanup()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
