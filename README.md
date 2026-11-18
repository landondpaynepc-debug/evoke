# Evoke Systems

Evoke Support opens a private ticket under category `1558547311590903831` and pings staff role `1560752530366144543`. Only staff can claim. Staff or the opener can confirm closure. Closure removes the opener’s individual access, moves the channel out of the open category, and preserves its messages and attachments for staff. Staff members retain access through their role. Each member can have one open ticket. Keep one bot instance running.

The bot publishes or refreshes its Components V2 support panel in `1566118218605662409` and its roster requirements in `1566120622294048819` when it connects. Keep the included banner.webp alongside bot.py.

## Run on Render

Set DISCORD_TOKEN as a secret environment variable on the existing evoke service. Use the token for the Evoke Systems Discord application. Set the bot username in the Discord Developer Portal. Invite the bot with the bot scope. Grant View Channels, Read Message History, Send Messages, Attach Files, Manage Channels and Manage Roles where it manages tickets. Place its role above the staff role. Make the staff role mentionable or give the bot Mention Everyone in the ticket channels.

Start command: `python bot.py`. Standard Python build command: `python -m pip install -r requirements.txt`. The included mix.exs also supports the existing service's `mix phx.digest` build command when Python and pip are installed. The service listens on PORT and serves readiness at /health. No privileged gateway intents required.
