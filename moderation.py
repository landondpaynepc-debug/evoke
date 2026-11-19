"""Dot commands for the Evoke guild. The caller filters guild and bot messages."""
import asyncio
import logging
import re
from datetime import timedelta

import discord
from discord import ui

log = logging.getLogger("evoke.commands")
afk = {}
record_lock = asyncio.Lock()
ALIASES = {"mute": "timeout", "unmute": "untimeout", "purge": "clear", "modhelp": "modcommands"}
PERMISSION = {
    "kick": "kick_members", "ban": "ban_members", "unban": "ban_members",
    "timeout": "moderate_members", "untimeout": "moderate_members",
    "warn": "moderate_members", "warnings": "moderate_members", "unwarn": "moderate_members",
    "clear": "manage_messages", "slowmode": "manage_channels",
    "lock": "manage_roles", "unlock": "manage_roles", "addrole": "manage_roles", "removerole": "manage_roles",
}
HELP = ("**Members**\n`.afk [reason]` · Run it again to return.\n"
        "**Moderation**\n`.warn @member reason` · `.warnings @member` · `.unwarn warning_id`\n"
        "`.kick @member [reason]` · `.ban @member [reason]` · `.unban user_id [reason]`\n"
        "`.timeout @member 10m [reason]` · `.untimeout @member` (also `.mute` / `.unmute`)\n"
        "`.clear 1–100` · `.slowmode 0–21600` seconds · `.lock` · `.unlock`\n"
        "`.addrole @member @role` · `.removerole @member @role`")


def card(title, body):
    view = ui.LayoutView(timeout=None)
    view.add_item(ui.Container(ui.TextDisplay(f"## {title}"), ui.TextDisplay(body)))
    return view


def snowflake(value):
    match = re.fullmatch(r"(?:<@(?:!|&)?(\d{15,20})>|(\d{15,20}))", value)
    if not match:
        raise ValueError("Use a Discord mention or numeric ID. See `.modcommands` for usage.")
    return int(match[1] or match[2])


def duration(value):
    match = re.fullmatch(r"([0-9]{1,8})([smhd])", value.lower())
    if match:
        seconds = int(match[1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match[2]]
        if 1 <= seconds <= 28 * 86400:
            return timedelta(seconds=seconds)
    raise ValueError("Use a duration from 1 second to 28 days, such as `10m` or `1d`.")


async def target_member(msg, arg, *, enforce=True):
    uid = snowflake(arg)
    member = msg.guild.get_member(uid) or await msg.guild.fetch_member(uid)
    if enforce:
        actor, bot = msg.author, msg.guild.me
        if (uid in (actor.id, bot.id, msg.guild.owner_id) or member.top_role >= bot.top_role
                or (actor.id != msg.guild.owner_id and member.top_role >= actor.top_role)):
            raise ValueError("You can only moderate members below both your role and the bot’s role.")
    return member


async def records(guild):
    name = "evoke-moderation-log"
    for channel in guild.text_channels:
        if channel.name == name:
            if channel.overwrites_for(guild.default_role).view_channel is not False:
                raise ValueError("Make the moderation log private before using it.")
            return channel
    return await guild.create_text_channel(name, overwrites={
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True)},
        reason="Store Evoke moderation records")


async def run_mod(msg, cmd, tail):
    channel, guild = msg.channel, msg.guild
    perm = PERMISSION[cmd]
    # Discord guild actions require guild-wide permissions, channel actions use effective overwrites.
    channel_action = cmd in ("clear", "lock", "unlock", "slowmode")
    for who in (msg.author, guild.me):
        perms = channel.permissions_for(who) if channel_action else who.guild_permissions
        if not getattr(perms, perm):
            raise ValueError(f"{who.display_name} needs **{perm.replace('_', ' ').title()}** for this command.")
    first, _, rest = tail.partition(" ")
    audit = f"{msg.author} ({msg.author.id}): {rest or 'No reason provided'}"[:500]
    if cmd == "clear":
        if not first.isdecimal() or not 1 <= int(first) <= 100:
            raise ValueError("Use `.clear 1–100`.")
        deleted = await channel.purge(limit=int(first), before=msg)
        return f"Deleted {len(deleted)} messages."
    if cmd == "slowmode":
        if not first.isdecimal() or not 0 <= int(first) <= 21600:
            raise ValueError("Use `.slowmode 0–21600` seconds.")
        await channel.edit(slowmode_delay=int(first), reason=audit)
        return f"Slowmode set to {first} seconds."
    if cmd in ("lock", "unlock"):
        if not isinstance(channel, discord.TextChannel):
            raise ValueError("Use this command in a text channel.")
        async with record_lock:
            ledger = await records(guild)
            marker = f"LOCK {channel.id} "
            saved = None
            async for entry in ledger.history(limit=None):
                if entry.author.id == guild.me.id and entry.content.startswith(marker):
                    saved = entry
                    break
            ow = channel.overwrites_for(guild.default_role)
            if cmd == "lock":
                if saved is None:
                    if ow.send_messages is False:
                        return "This channel is already locked."
                    saved = await ledger.send(marker + str(ow.send_messages))
                ow.send_messages = False
            else:
                if saved is None:
                    return "There is no Evoke lock to undo in this channel."
                ow.send_messages = True if saved.content == marker + "True" else None
            await channel.set_permissions(guild.default_role, overwrite=None if ow.is_empty() else ow, reason=audit)
            if cmd == "unlock":
                await saved.delete()
            return f"Channel {cmd}ed."
    if cmd == "unban":
        user = discord.Object(id=snowflake(first))
        await guild.unban(user, reason=audit)
        return f"Unbanned user `{user.id}`."
    if cmd == "unwarn":
        async with record_lock:
            ledger = await records(guild)
            entry = await ledger.fetch_message(snowflake(first))
            if entry.author.id != guild.me.id or not entry.content.startswith("WARN "):
                raise ValueError("That ID is not an Evoke warning.")
            await entry.delete()
        return "Warning removed."
    member = await target_member(msg, first, enforce=cmd != "warnings")
    if cmd in ("warn", "warnings"):
        async with record_lock:
            ledger = await records(guild)
            marker = f"WARN {member.id} "
            if cmd == "warn":
                if not rest.strip():
                    raise ValueError("Use `.warn @member reason`.")
                entry = await ledger.send(f"{marker}{msg.author.id}\n{rest[:1400]}")
                return f"Warned {member.mention}. Warning ID: `{entry.id}`."
            recent = []
            async for entry in ledger.history(limit=None):
                if entry.author.id == guild.me.id and entry.content.startswith(marker):
                    recent.append(f"`{entry.id}` · {entry.content.partition(chr(10))[2][:125]}")
                    if len(recent) == 10:
                        break
            return f"Latest warnings for {member.mention}:\n" + ("\n".join(recent) or "None.")
    if cmd in ("addrole", "removerole"):
        role = guild.get_role(snowflake(rest.split(" ", 1)[0]))
        if (not role or not role.is_assignable() or (msg.author.id != guild.owner_id and
                (role >= msg.author.top_role or not role.permissions.is_subset(msg.author.guild_permissions)))):
            raise ValueError("Choose a role you and the bot can manage.")
        method = member.add_roles if cmd == "addrole" else member.remove_roles
        await method(role, reason=audit)
        return f"Updated {member.mention}’s {role.mention} role."
    if cmd == "kick":
        await member.kick(reason=audit)
        return f"Kicked {member.mention}."
    if cmd == "ban":
        await guild.ban(member, reason=audit, delete_message_seconds=0)
        return f"Banned {member.mention}."
    if cmd == "timeout":
        period = duration(rest.partition(" ")[0])
        await member.timeout(period, reason=audit)
        return f"Timed out {member.mention} for {period}."
    await member.timeout(None, reason=audit)
    return f"Removed the timeout from {member.mention}."


async def handle_command(msg):
    if not msg.content.startswith("."):
        return
    name, _, tail = msg.content.strip()[1:].partition(" ")
    cmd = ALIASES.get(name.lower(), name.lower())
    if cmd == "afk":
        key = (msg.guild.id, msg.author.id)
        if key in afk:
            del afk[key]
            await msg.channel.send(f"✅ Welcome back {msg.author.mention}, AFK cleared.", delete_after=10)
        else:
            since = int(msg.created_at.timestamp())
            reason = discord.utils.escape_markdown(tail.strip()[:300] or "Away from keyboard")
            afk[key] = (since, reason)
            await msg.channel.send(view=card("AFK Enabled", f"👤 **Member:** {msg.author.mention}\n"
                f"🔗 **Reason:** {reason}\n⏱️ **Since:** <t:{since}:R>\n-# Run .afk again to return."), delete_after=10)
        if msg.channel.permissions_for(msg.guild.me).manage_messages:
            await msg.delete(delay=10)
        return
    if cmd == "modcommands":
        await msg.channel.send(view=card("Evoke commands", HELP))
        return
    if cmd not in PERMISSION:
        return
    try:
        response = await run_mod(msg, cmd, tail.strip())
    except ValueError as error:
        response = str(error)
    except discord.NotFound:
        response = "That member, ban, or moderation record could not be found."
    except discord.Forbidden:
        response = "Discord denied that action. Check the bot’s permissions and role position."
    except discord.HTTPException:
        log.exception("Discord rejected moderation command %s", cmd)
        response = "Discord could not complete that action."
    await msg.channel.send(response, allowed_mentions=discord.AllowedMentions.none())
