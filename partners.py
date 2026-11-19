"""Evoke partner directory and staff intake form."""
import asyncio
import re
from uuid import uuid4

import discord
from discord import ui

CHANNEL = 1569495754060333116
TITLE = "# Evoke Partners"
SEED = ("Esports Business", "rxgyabAHyK")
INVITE = re.compile(r"(?:https?://)?(?:www\.)?(?:discord\.gg/|discord(?:app)?\.com/invite/)([A-Za-z0-9_-]{2,100})/?", re.I)
lock = asyncio.Lock()
directory = {}
message = None
storage = None
page = 0


def parse_invite(text):
    match = INVITE.fullmatch(text.strip())
    if not match:
        raise ValueError("Enter a Discord invite such as https://discord.gg/yourcode.")
    return match[1]


def directory_view(make_panel, number):
    entries = sorted(directory.values(), key=lambda item: item[0].casefold())
    pages = max(1, (len(entries) + 24) // 25)
    number = max(0, min(number, pages - 1))
    options = [discord.SelectOption(label=name, value=code) for name, code in entries[number*25:(number+1)*25]]
    items = [TITLE, "Choose a partner below to get their Discord invite.",
             ui.ActionRow(ui.Select(custom_id=f"partner:select:{uuid4().hex}", placeholder="Choose a partner server", options=options))]
    if pages > 1:
        items.append(ui.ActionRow(
            ui.Button(label="Previous", custom_id=f"partner:page:{number-1}", disabled=number==0),
            ui.Button(label=f"{number+1} / {pages}", custom_id="partner:page-count", disabled=True),
            ui.Button(label="Next", custom_id=f"partner:page:{number+1}", disabled=number+1==pages)))
    return make_panel(*items)


async def setup(guild, store, make_panel, upload):
    global storage, message, page
    async with lock:
        storage = store
        channel = await guild.fetch_channel(CHANNEL)
        if not isinstance(channel, discord.TextChannel):
            raise ValueError("Partners destination must be a text channel.")
        directory.clear()
        directory[SEED[0].casefold()] = SEED
        async for record in store.history(limit=None, oldest_first=True):
            if record.author.id == guild.me.id and record.content.startswith("EVOKE-PARTNER\n"):
                fields = record.content.split("\n")
                if len(fields) == 3:
                    _, name, code = fields
                    directory[name.casefold()] = (name, code)
        page = 0
        view = directory_view(make_panel, page)
        async for existing in channel.history(limit=None):
            if existing.author.id == guild.me.id and any(getattr(child, "content", "") == TITLE
                    for component in existing.components for child in getattr(component, "children", [])):
                message = await existing.edit(view=view, attachments=[upload()])
                break
        else:
            message = await channel.send(view=view, file=upload(), nonce=f"ep{CHANNEL}")
        return len(directory)


async def command(msg, staff_id):
    if not any(role.id == staff_id for role in msg.author.roles):
        await msg.reply("Only Evoke staff can add partners.", mention_author=False, nonce=f"ap{msg.id}", delete_after=10)
        return
    view = ui.LayoutView(timeout=None)
    view.add_item(ui.ActionRow(ui.Button(label="Add partner", custom_id=f"partner:open:{msg.author.id}",
                                        style=discord.ButtonStyle.primary)))
    await msg.reply(view=view, mention_author=False, nonce=f"ap{msg.id}", delete_after=120)


async def handle(i, staff_id, make_panel):
    global page
    action = i.data["custom_id"].split(":")[1:]
    if action[0] in ("open", "save"):
        if not any(role.id == staff_id for role in i.user.roles) or action[1] != str(i.user.id):
            await i.response.send_message("Only the staff member who ran .addpartner can use this form.", ephemeral=True)
            return
        if action[0] == "open":
            modal = ui.Modal(title="Add an Evoke partner", custom_id=f"partner:save:{i.user.id}")
            for label, key, limit in [("Server name", "name", 100), ("Discord invite", "invite", 160)]:
                modal.add_item(ui.Label(text=label, component=ui.TextInput(custom_id=key, max_length=limit)))
            await i.response.send_modal(modal)
            return
    elif i.channel_id != CHANNEL or message is None or i.message.id != message.id:
        await i.response.send_message("Use the current Evoke Partners panel.", ephemeral=True)
        return
    await i.response.defer(ephemeral=True, thinking=True)
    reply_view = None
    try:
        async with lock:
            if message is None or storage is None:
                raise ValueError("The partner directory is starting. Please try again shortly.")
            if action[0] == "save":
                fields = {row["component"]["custom_id"]: row["component"]["value"] for row in i.data["components"]}
                name = " ".join(fields["name"].split())
                code = parse_invite(fields["invite"])
                if not 1 <= len(name) <= 100:
                    raise ValueError("Enter a server name between 1 and 100 characters.")
                entry = (name, code)
                current = directory.get(name.casefold())
                if any(other_code == code and key != name.casefold() for key, (_, other_code) in directory.items()):
                    raise ValueError("That invite is already listed under another partner.")
                if current != entry:
                    await storage.send(f"EVOKE-PARTNER\n{name}\n{code}", allowed_mentions=discord.AllowedMentions.none())
                    directory[name.casefold()] = entry
                page = sorted(directory).index(name.casefold()) // 25
                result = f"Saved **{discord.utils.escape_markdown(name)}** to the partner directory."
            elif action[0] == "page":
                page = max(0, min(int(action[1]), (len(directory)-1)//25))
                result = f"Showing page {page+1}."
            elif action[0] == "select":
                code = i.data["values"][0]
                entry = next((item for item in directory.values() if item[1] == code), None)
                if not entry:
                    raise ValueError("That invite has changed. Select the partner again.")
                result = f"**{discord.utils.escape_markdown(entry[0])}**"
                reply_view = ui.View(timeout=None)
                reply_view.add_item(ui.Button(label="Join server", url=f"https://discord.gg/{code}"))
            else:
                raise ValueError("Unknown partner action.")
            await message.edit(view=directory_view(make_panel, page))
    except ValueError as error:
        result = str(error)
    except discord.HTTPException:
        result = "Discord could not finish updating the partner panel. Please retry."
    await i.edit_original_response(content=result, view=reply_view, allowed_mentions=discord.AllowedMentions.none())
