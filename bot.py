import logging

import discord

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("discord.gateway").setLevel(logging.DEBUG)

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

debug_guilds = [int(config.DEBUG_GUILD)] if config.DEBUG_GUILD else None
bot = discord.Bot(intents=intents, debug_guilds=debug_guilds)


@bot.event
async def on_ready():
    print(f"Бот запущен: {bot.user} ({bot.user.id})")


bot.load_extension("cogs.jester")
bot.load_extension("cogs.chat")

if __name__ == "__main__":
    if not config.DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN не задан в .env")
    if not config.GROQ_API_KEY:
        raise SystemExit("GROQ_API_KEY не задан в .env")
    import keepalive
    keepalive.start()
    bot.run(config.DISCORD_TOKEN)
