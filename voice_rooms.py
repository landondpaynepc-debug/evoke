"""Owner-controlled temporary voice rooms for Evoke."""
import asyncio
import logging

import discord
from discord import ui

PANEL = 1573106537361182853
DEST = 1573106789761818694
LOG = logging.getLogger("evoke.voice")
lock = asyncio.Lock()
rooms = {}  # channel ID -> (owner ID, private registry message)
timers = {}
lobby = None
category = None
registry = None
bot = None
panel_msg = None
loaded = False


def controls():
    def btn(label, action, icon, primary=False):
        return ui.Button(label=label, emoji=icon, custom_id="evc:" + action,
                         style=discord.ButtonStyle.primary if primary else discord.ButtonStyle.secondary)
    v = ui.LayoutView(timeout=None)
    v.add_item(ui.Container(ui.MediaGallery(discord.MediaGalleryItem(media="attachment://banner.webp")),
        ui.TextDisplay("# Voice Rooms"), ui.TextDisplay(f"Join {lobby.mention} or select **Create VC** to open your private room. "
            "Use **Invite** to give a member access. Empty rooms close after 30 seconds."),
        ui.TextDisplay(f"**Rooms:** {len(rooms)}"),
        ui.ActionRow(btn("Create VC","create","➕",True),btn("Invite","invite","👤"),btn("Lock","lock","🔒"),
                     btn("Unlock","unlock","🔓")),
        ui.ActionRow(btn("Claim","claim","👑"),btn("Rename","rename","✏️"),btn("Limit","limit","👥"))))
    return v


async def refresh():
    if panel_msg:
        try:
            await panel_msg.edit(view=controls())
        except discord.HTTPException:
            LOG.exception("Could not refresh voice room count")


def empty_timer(ch):
    task = timers.get(ch.id)
    if ch.voice_states:
        if task:
            task.cancel()
            timers.pop(ch.id, None)
    elif task is None or task.done():
        timers[ch.id] = asyncio.create_task(expire(ch))


async def expire(ch):
    try:
        await asyncio.sleep(30)
        async with lock:
            cached = ch.guild.get_channel(ch.id)
            if not bot.is_ready() or ch.guild.unavailable or ch.id not in rooms or (cached and cached.voice_states):
                return
            if cached:
                await cached.delete(reason="Evoke VC empty for 30 seconds")
            _, record = rooms.pop(ch.id)
            await record.delete()
            await refresh()
    except discord.NotFound:
        pass
    except discord.HTTPException:
        LOG.exception("Could not clean up voice room %s", ch.id)
    finally:
        if timers.get(ch.id) is asyncio.current_task():
            timers.pop(ch.id, None)


async def setup(client, guild, store, banner):
    global bot, registry, category, lobby, loaded, panel_msg
    async with lock:
        bot, registry = client, store
        dest = await guild.fetch_channel(DEST) if DEST else None
        target = await guild.fetch_channel(PANEL)
        if not isinstance(target, discord.TextChannel):
            raise RuntimeError("VC panel destination must be a text channel")
        if dest is None or isinstance(dest, discord.CategoryChannel):
            category = dest
            lobby = next((ch for ch in guild.voice_channels if ch.category_id == DEST and
                          ch.name in ("➕ Create VC", "Create VC")), None)
            if lobby is None:
                allow = discord.PermissionOverwrite(view_channel=True, connect=True)
                lobby = await guild.create_voice_channel("➕ Create VC", category=category, overwrites={
                    guild.default_role: allow, guild.me: discord.PermissionOverwrite(view_channel=True, connect=True, move_members=True)})
        elif isinstance(dest, discord.VoiceChannel):
            lobby, category = dest, dest.category
        else:
            raise RuntimeError("Voice room destination must be a category or voice lobby")
        if not loaded:
            async for rec in store.history(limit=None):
                fields = rec.content.split()
                if rec.author.id == guild.me.id and len(fields)==3 and fields[0]=="EVOKE-ROOM" and all(x.isdecimal() for x in fields[1:]):
                    cid, owner = map(int,fields[1:])
                    if isinstance(guild.get_channel(cid), discord.VoiceChannel):
                        rooms[cid] = (owner, rec)
                    else:
                        await rec.delete()
            loaded = True
        for cid in list(rooms):
            ch = guild.get_channel(cid)
            if ch:
                empty_timer(ch)
        async for msg in target.history(limit=None):
            if msg.author.id == guild.me.id and any(getattr(child,"content","")=="# Voice Rooms"
                    for top in msg.components for child in getattr(top,"children",[])):
                panel_msg = await msg.edit(view=controls(), attachments=[banner()])
                break
        else:
            panel_msg = await target.send(view=controls(),file=banner())
        LOG.info("Voice Rooms panel ready in %s; destination %s; lobby %s; restored %s rooms", PANEL,DEST,lobby.id,len(rooms))


def owned(member):
    return next((member.guild.get_channel(cid) for cid,(uid,_) in rooms.items()
                 if uid==member.id and member.guild.get_channel(cid)),None)


async def create(member, move=False):
    ch=owned(member)
    if ch is None:
        guild=member.guild
        allow=discord.PermissionOverwrite(view_channel=True,connect=True,speak=True)
        ch=await guild.create_voice_channel(f"{member.display_name}’s VC"[:100],category=category,
            overwrites={guild.default_role:discord.PermissionOverwrite(view_channel=False,connect=False),
                        guild.me:discord.PermissionOverwrite(view_channel=True,connect=True,move_members=True),
                        member:allow},reason=f"Evoke private room for {member.id}")
        try:
            record=await registry.send(f"EVOKE-ROOM {ch.id} {member.id}")
        except discord.HTTPException:
            await ch.delete(reason="Room registration failed")
            raise
        rooms[ch.id]=(member.id,record)
        empty_timer(ch)
        await refresh()
    if move and member.voice and member.voice.channel and member.voice.channel.id==lobby.id:
        await member.move_to(ch,reason="Joined Evoke Create VC")
    return ch


async def voice_event(member,before,after):
    if not loaded or before.channel==after.channel:
        return
    async with lock:
        for ch in (before.channel,after.channel):
            if ch and ch.id in rooms:
                empty_timer(ch)
        if after.channel and after.channel.id==lobby.id and not member.bot:
            try:
                await create(member,move=True)
            except discord.HTTPException:
                LOG.exception("Could not create or move member into a private VC")


async def handle(i):
    action=i.data["custom_id"].split(":")[1]
    if i.channel_id!=PANEL or not loaded:
        await i.response.send_message("Use the Voice Rooms panel once it is ready.",ephemeral=True)
        return
    if action in ("rename","limit","invite"):
        if not owned(i.user):
            await i.response.send_message("Create a VC first to use its owner controls.",ephemeral=True)
            return
        if action=="invite":
            view=ui.LayoutView(timeout=120)
            view.add_item(ui.ActionRow(ui.UserSelect(custom_id="evc:permit",placeholder="Give a member access",max_values=1)))
            await i.response.send_message(view=view,ephemeral=True)
        else:
            modal=ui.Modal(title="Rename VC" if action=="rename" else "Set member limit",custom_id="evc:set-"+action)
            modal.add_item(ui.Label(text="Room name" if action=="rename" else "Limit (0–99; 0 is unlimited)",
                component=ui.TextInput(custom_id="value",max_length=80 if action=="rename" else 2)))
            await i.response.send_modal(modal)
        return
    await i.response.defer(ephemeral=True,thinking=True)
    try:
        async with lock:
            if action=="create":
                ch=await create(i.user)
                result=f"Your room is {ch.mention}. Join within 30 seconds while it’s empty."
            elif action=="claim":
                ch=i.user.voice.channel if i.user.voice else None
                if not ch or ch.id not in rooms:
                    raise ValueError("Join the managed room you want to claim.")
                owner,rec=rooms[ch.id]
                if owner in ch.voice_states:
                    raise ValueError("The current owner is still in the room.")
                if owned(i.user):
                    raise ValueError("You already own a room.")
                await ch.set_permissions(i.user,view_channel=True,connect=True,speak=True)
                await rec.edit(content=f"EVOKE-ROOM {ch.id} {i.user.id}")
                rooms[ch.id]=(i.user.id,rec)
                result=f"You now own {ch.mention}."
            else:
                ch=owned(i.user)
                if not ch:
                    raise ValueError("Only the room owner can use this control.")
                if action in ("lock","unlock"):
                    allow=action=="unlock"
                    await ch.set_permissions(i.guild.default_role,view_channel=allow,connect=allow)
                    result="Your room is public." if allow else "Your room is private. Invited members retain access."
                elif action=="permit":
                    member=await i.guild.fetch_member(int(i.data["values"][0]))
                    await ch.set_permissions(member,view_channel=True,connect=True,speak=True)
                    result=f"{member.mention} can now join {ch.mention}."
                elif action in ("set-limit","set-rename"):
                    value=i.data["components"][0]["component"]["value"].strip()
                    if action=="set-limit":
                        if not value.isascii() or not value.isdigit() or int(value)>99:
                            raise ValueError("Enter 0 through 99.")
                        await ch.edit(user_limit=int(value))
                    else:
                        if not value:
                            raise ValueError("Enter a room name.")
                        await ch.edit(name=value[:80])
                    result=f"Updated {ch.mention}."
                else:
                    raise ValueError("Unknown room control.")
    except ValueError as e:
        result=str(e)
    except discord.HTTPException:
        LOG.exception("Voice room action %s failed",action)
        result="Discord could not finish that change. Check the bot’s voice and channel permissions."
    await i.edit_original_response(content=result)
