import logging
import time

import discord

import config
import keepalive

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("discord.gateway").setLevel(logging.DEBUG)

if not discord.opus.is_loaded():
    for _name in ("opus", "libopus.so.0", "libopus.so"):
        try:
            discord.opus.load_opus(_name)
            break
        except Exception:
            pass
keepalive.VERSION += f" opus={int(discord.opus.is_loaded())}"

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


def _on_utterance(data):
    cog = bot.get_cog("Jester")
    if cog and bot.loop and not bot.loop.is_closed():
        import asyncio
        asyncio.run_coroutine_threadsafe(cog.handle_utterance(data), bot.loop)


keepalive.on_utterance = _on_utterance

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
