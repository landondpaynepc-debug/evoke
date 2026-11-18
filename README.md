# Evoke Systems

Evoke Support opens a private ticket under category `1558547311590903831` and pings staff role `1565166580411924610`. Only staff can claim. Staff or the opener can confirm closure. Confirming closure permanently deletes the ticket channel and its message history. Each member can have one open ticket. Keep one bot instance running.

The bot publishes or refreshes its Components V2 support panel in `1566118218605662409` and its roster requirements in `1566120622294048819` when it connects. Keep the included banner.webp alongside bot.py.

## Run on Render

Set DISCORD_TOKEN as a secret environment variable on the existing evoke service. Use the token for the Evoke Systems Discord application. Set the bot username in the Discord Developer Portal. Invite the bot with the bot scope. Grant View Channels, Read Message History, Send Messages, Attach Files, Manage Channels and Manage Roles where it manages tickets. Place its role above the staff role. Make the staff role mentionable or give the bot Mention Everyone in the ticket channels.

### Existing Evoke private service

This service uses Render’s Elixir runtime. Set both commands together.

| Setting | Value |
| --- | --- |
| Build Command | `mix phx.digest` |
| Start Command | `LD_LIBRARY_PATH="$PWD/.python-runtime/lib" ./.python-runtime/bin/python3 bot.py` |

The build copies Python into the project, installs the bot dependencies and checks that the bundled interpreter can import them. The service listens on PORT and serves readiness at /health.

For a native Python service, use `python -m pip install -r requirements.txt` to build and `python bot.py` to start. No privileged gateway intents required.
