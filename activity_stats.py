"""Evoke member activity, grouped by UTC calendar day in public channels."""
import asyncio
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import gzip
import io
import json
import logging
import re
import time

import discord
from discord import ui
from PIL import Image, ImageDraw, ImageFont, ImageOps

LOG = logging.getLogger("evoke.stats")
DAYS = (1, 7, 14)
STORE = "evoke-activity-data"
data = {}
started = None
storage = None
last_backup = None
ready = asyncio.Event()
init_lock = asyncio.Lock()
voice = {}
last_tick = None
last_save = 0
generation = 0
saved_generation = 0


def public(channel):
    return (isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel))
            and channel.permissions_for(channel.guild.default_role).view_channel)


def add(day, member_id, channel_id, metric, amount):
    global generation
    member = data.setdefault(str(day), {}).setdefault(str(member_id), {})
    pair = member.setdefault(str(channel_id), [0, 0])
    pair[metric] += amount
    generation += 1


def prune(today):
    cutoff = str(today - timedelta(days=13))
    for day in list(data):
        if day < cutoff:
            del data[day]


async def initialize(guild):
    global started, storage, last_backup
    async with init_lock:
        if ready.is_set():
            return
        storage = discord.utils.get(guild.text_channels, name=STORE)
        if storage is None:
            storage = await guild.create_text_channel(STORE, overwrites={
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                       attach_files=True, read_message_history=True)})
        if storage.permissions_for(guild.default_role).view_channel:
            raise RuntimeError("The Evoke activity storage channel must be private.")
        async for m in storage.history(limit=None):
            if m.author.id == guild.me.id and m.attachments and m.attachments[0].filename == "activity.json.gz":
                state = json.loads(gzip.decompress(await m.attachments[0].read()))
                data.update(state["days"])
                started = state["started"]
                last_backup = m
                break
        started = started or str(discord.utils.utcnow().date())
        prune(discord.utils.utcnow().date())
        ready.set()
        LOG.info("Activity tracking ready; first tracked UTC date %s", started)


async def checkpoint():
    global last_backup, saved_generation, last_save
    if time.monotonic() - last_save < 300 or generation == saved_generation:
        return
    last_save = time.monotonic()
    revision = generation
    payload = gzip.compress(json.dumps({"started": started, "days": data}, separators=(",", ":")).encode())
    new = await storage.send(file=discord.File(io.BytesIO(payload), filename="activity.json.gz"))
    previous, last_backup = last_backup, new
    saved_generation = revision
    if previous:
        try:
            await previous.delete()
        except discord.NotFound:
            pass


def voice_reset():
    global last_tick
    voice.clear()
    last_tick = None


def accrue(now):
    global last_tick
    if last_tick is not None:
        start = last_tick
        if 0 < now - start <= 120:
            while start < now:
                day = datetime.fromtimestamp(start, timezone.utc).date()
                midnight = int(datetime.combine(day + timedelta(days=1), datetime.min.time(), timezone.utc).timestamp())
                stop = min(now, midnight)
                for user_id, channel_id in voice.items():
                    add(day, user_id, channel_id, 1, stop - start)
                start = stop
    last_tick = now


def sync_voice(guild):
    accrue(int(time.time()))
    voice.clear()
    for channel in [*guild.voice_channels, *guild.stage_channels]:
        if public(channel):
            voice.update({m.id: channel.id for m in channel.members if not m.bot})


def voice_event(member, before, after):
    if member.bot or not ready.is_set() or before.channel == after.channel:
        return
    accrue(int(time.time()))
    voice.pop(member.id, None)
    if after.channel and public(after.channel):
        voice[member.id] = after.channel.id


def aggregate(uid, days, today):
    totals = [Counter(), Counter()]
    channels = [Counter(), Counter()]
    series = []
    for back in reversed(range(days)):
        bucket = data.get(str(today - timedelta(days=back)), {})
        daily = [0, 0]
        for user, entries in bucket.items():
            for ch, values in entries.items():
                for k, value in enumerate(values):
                    totals[k][user] += value
                    if user == uid:
                        channels[k][ch] += value
                        daily[k] += value
        series.append(daily)
    ranks = [1 + sum(n > t[uid] for n in t.values()) if t[uid] else None for t in totals]
    return [t[uid] for t in totals], ranks, channels, series


def elapsed(seconds):
    minutes = seconds // 60
    if not minutes:
        return f"{seconds}s"
    return f"{minutes // 60}h {minutes % 60}m" if minutes >= 60 else f"{minutes}m"


def face(size, bold=False):
    fonts = ["DejaVuSans-Bold.ttf", "arialbd.ttf"] if bold else ["DejaVuSans.ttf", "arial.ttf"]
    for family in fonts:
        try:
            return ImageFont.truetype(family, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def draw_card(profile, avatar, windows, days, tops, today):
    im = Image.new("RGB", (960, 530), "#19191f")
    d = ImageDraw.Draw(im)
    white, muted, pink, blue = "#f5f3f7", "#b4b0bd", "#ff53b7", "#68d5e7"
    def txt(x, y, value, size=18, color=white, bold=False):
        d.text((x,y), str(value), font=face(size,bold), fill=color)
    def box(x,y,w,h):
        d.rounded_rectangle((x,y,x+w,y+h),radius=14,fill="#292930")
    def fit(value, width, size):
        f=face(size,True)
        while value and d.textlength(value,font=f)>width:
            value=value[:-1]
        return value
    box(16,16,928,96)
    if avatar:
        pic=ImageOps.fit(Image.open(io.BytesIO(avatar)).convert("RGB"), (64,64))
        mask=Image.new("L",(64,64)); ImageDraw.Draw(mask).ellipse((0,0,63,63),fill=255)
        im.paste(pic,(32,32),mask)
    txt(110,28,fit(profile[0],360,26),26,bold=True)
    txt(110,67,fit(profile[1],350,17),17,muted)
    for x,label,val in [(500,"ACCOUNT CREATED",profile[2]),(729,"JOINED EVOKE",profile[3])]:
        txt(x,34,label,14,muted,True);txt(x,61,val,18)
    _,ranks,_,series=windows[days]
    for x,title in [(16,"Server ranks"),(330,"Messages"),(642,"Voice activity")]:
        box(x,126,302,160);txt(x+16,138,title,22,bold=True)
    for idx,label in enumerate(("Messages","Voice")):
        y=189+idx*37;txt(32,y,label,18,muted);txt(204,y,f"#{ranks[idx]}" if ranks[idx] else "—",19,bold=True)
    for idx,n in enumerate(DAYS):
        y=184+idx*31;values=windows[n][0]
        for x,val in [(346,f"{values[0]:,}"),(658,elapsed(values[1]))]:
            txt(x,y,f"{n}d",17,muted);txt(x+85,y,val,18,bold=True)
    box(16,300,445,170);txt(32,313,"Top channels",22,bold=True)
    for row,(label,val) in enumerate(tops):
        txt(32,360+row*44,fit(label,270,18),18);txt(310,360+row*44,val,17,muted)
    box(474,300,470,170);txt(490,313,f"Daily activity · {days}d",22,bold=True)
    for k,(color,label) in enumerate([(pink,"Messages"),(blue,"Voice")]):
        y=365+k*48;txt(490,y,label,15,color,True)
        vals=[v[k] for v in series];peak=max(max(vals),1)
        left,right,base=598,922,y+25
        d.line((left,base,right,base),fill="#494550")
        points=[(left+i*(right-left)/max(1,len(vals)-1),base-28*v/peak) for i,v in enumerate(vals)]
        if len(points)>1:d.line(points,fill=color,width=3)
        for x,p in points:d.ellipse((x-2,p-2,x+2,p+2),fill=color)
    txt(20,488,f"UTC · {today:%b %d, %Y}   |   Tracking since {started}",16,muted)
    txt(770,486,"EVOKE SYSTEMS",16,pink,True)
    out=io.BytesIO();im.save(out,format="PNG");return out.getvalue()


async def card(guild, member, days):
    today=discord.utils.utcnow().date()
    accrue(int(time.time()))
    windows={n:aggregate(str(member.id),n,today) for n in DAYS}
    tops=[]
    for k,counts in enumerate(windows[days][2]):
        for cid,value in counts.most_common():
            ch=guild.get_channel(int(cid))
            if value and ch and public(ch):
                tops.append(("#"+ch.name if k==0 else ch.name, f"{value:,} msgs" if k==0 else elapsed(value)))
                break
        else:
            tops.append(("Text" if k==0 else "Voice","No data"))
    try:
        avatar=await member.display_avatar.with_size(128).with_format("png").read()
    except discord.HTTPException:
        avatar=None
    dates=[t.strftime("%b %d, %Y") if t else "Unknown" for t in (member.created_at,member.joined_at)]
    profile=[member.display_name,guild.name,*dates]
    png=await asyncio.to_thread(draw_card,profile,avatar,windows,days,tops,today)
    view=ui.LayoutView(timeout=None)
    view.add_item(ui.Container(ui.MediaGallery(discord.MediaGalleryItem(media="attachment://activity.png",
        description=f"{member.display_name}: {windows[days][0][0]} messages and {elapsed(windows[days][0][1])} voice in {days} UTC days.")),
        ui.ActionRow(*(ui.Button(label=f"{n}d",custom_id=f"evstats:{member.id}:{n}:window",disabled=n==days)
                       for n in DAYS),ui.Button(label="Refresh",custom_id=f"evstats:{member.id}:{days}:refresh"))))
    return view,discord.File(io.BytesIO(png),filename="activity.png")


async def message_event(msg):
    if public(msg.channel) and ready.is_set():
        add(msg.created_at.date(),msg.author.id,msg.channel.id,0,1)
    parts=msg.content.split()
    if not parts or parts[0].lower() not in (".u","s?u"):
        return
    if not ready.is_set():
        await msg.reply("Activity tracking is starting. Try again shortly.", mention_author=False)
        return
    member=msg.author
    if len(parts)>1:
        match=re.fullmatch(r"(?:<@!?(\d{15,20})>|(\d{15,20}))",parts[1])
        if len(parts)!=2 or not match:
            await msg.reply("Use `.u` or `.u @member`.", mention_author=False)
            return
        uid=int(match[1] or match[2])
        try:
            member=msg.guild.get_member(uid) or await msg.guild.fetch_member(uid)
        except discord.NotFound:
            await msg.reply("That member is no longer in Evoke.",mention_author=False)
            return
    view,file=await card(msg.guild,member,14)
    await msg.reply(view=view,file=file,mention_author=False)


async def interaction(i):
    parts=i.data["custom_id"].split(":")
    await i.response.defer()
    if not ready.is_set():
        await i.followup.send("Activity tracking is starting.",ephemeral=True)
        return
    try:
        member=i.guild.get_member(int(parts[1])) or await i.guild.fetch_member(int(parts[1]))
        view,file=await card(i.guild,member,int(parts[2]))
        await i.message.edit(view=view,attachments=[file])
    except discord.NotFound:
        await i.followup.send("This card or member is no longer available.",ephemeral=True)
