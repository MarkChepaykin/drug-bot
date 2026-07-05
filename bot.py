import logging
import time

import discord

import config
import keepalive

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("discord.gateway").setLevel(logging.DEBUG)

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

debug_guilds = [int(config.DEBUG_GUILD)] if config.DEBUG_GUILD else None
bot = discord.Bot(intents=intents, debug_guilds=debug_guilds)


@bot.event
async def on_ready():
    keepalive.status = "online"
    print(f"Бот запущен: {bot.user} ({bot.user.id})")


bot.load_extension("cogs.jester")
bot.load_extension("cogs.chat")

if __name__ == "__main__":
    if not config.DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN не задан в .env")
    if not config.GROQ_API_KEY:
        raise SystemExit("GROQ_API_KEY не задан в .env")
    keepalive.start()
    try:
        bot.run(config.DISCORD_TOKEN)
    except discord.HTTPException as e:
        if e.status == 429:
            # Cloudflare забанил IP хостинга — не долбим рестартами, ждём и пробуем снова
            keepalive.status = "banned_429"
            print("[fatal] Discord 429 (Cloudflare бан IP) — жду 10 минут перед рестартом", flush=True)
            time.sleep(600)
        raise
