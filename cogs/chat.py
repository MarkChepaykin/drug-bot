from collections import defaultdict, deque

import discord
from discord.ext import commands

from services import llm


class Chat(commands.Cog):
    """Болталка: отвечает, когда бота упоминают (@бот текст)."""

    def __init__(self, bot):
        self.bot = bot
        self.history = defaultdict(lambda: deque(maxlen=10))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not self.bot.user:
            return
        jester = self.bot.get_cog("Jester")
        session = jester.sessions.get(message.guild.id) if (jester and message.guild) else None
        if session and session.active and message.channel.id == session.text_channel.id:
            return  # пока бот в войсе, в этом канале отвечает голосом Jester
        me = message.guild.me if message.guild else None
        bot_roles = [r for r in me.roles if r.managed] if me else []
        mentioned = self.bot.user in message.mentions or any(r in message.role_mentions for r in bot_roles)
        if not mentioned:
            return
        content = message.content
        for mention in (f"<@{self.bot.user.id}>", f"<@!{self.bot.user.id}>"):
            content = content.replace(mention, "")
        for r in bot_roles:
            content = content.replace(f"<@&{r.id}>", "")
        content = content.strip()
        if not content:
            return
        hist = self.history[message.channel.id]
        hist.append({"role": "user", "content": content})
        async with message.channel.typing():
            reply = await llm.chat(list(hist))
        hist.append({"role": "assistant", "content": reply})
        await message.reply(reply)


def setup(bot):
    bot.add_cog(Chat(bot))
