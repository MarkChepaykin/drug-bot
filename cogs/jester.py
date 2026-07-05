import asyncio
import os
import tempfile
import time
from collections import deque

import discord
from discord.ext import commands

import config
from services import llm, tts

# Прослушка разговора ВЫКЛЮЧЕНА: приём голоса сломан в py-cord 2.8
# (DAVE E2EE, https://github.com/Pycord-Development/pycord/issues/3139).
# Вместо ушей — текстовый канал: пока бот в войсе, он голосом отвечает
# на сообщения в канале, откуда его позвали, и подшучивает по теме беседы.


class JesterSession:
    def __init__(self, voice_client, text_channel):
        self.vc = voice_client
        self.text_channel = text_channel
        self.active = True
        self.last_err = 0.0
        self.history = deque(maxlen=12)
        self.fresh_lines = 0
        self.speak_lock = asyncio.Lock()


class Jester(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.sessions: dict[int, JesterSession] = {}

    @commands.Cog.listener()
    async def on_application_command(self, ctx):
        print(f"[cmd] получена команда /{ctx.command} от {ctx.author}", flush=True)

    @commands.Cog.listener()
    async def on_application_command_error(self, ctx, error):
        print(f"[cmd-error] /{ctx.command}: {error!r}", flush=True)

    @discord.slash_command(description="Зайти в твой голосовой канал и общаться голосом")
    async def join(self, ctx: discord.ApplicationContext):
        if not ctx.author.voice:
            await ctx.respond("Ты не в голосовом канале.", ephemeral=True)
            return
        if ctx.guild.id in self.sessions:
            await ctx.respond("Я уже тут.", ephemeral=True)
            return
        await ctx.defer()
        channel = ctx.author.voice.channel
        try:
            vc = await channel.connect(timeout=20)
        except Exception as e:
            await ctx.followup.send(f"Не смог подключиться к голосу ({type(e).__name__}: {e})")
            return
        self.sessions[ctx.guild.id] = JesterSession(vc, ctx.channel)
        await ctx.followup.send(
            f"Зашёл в **{channel.name}**. Пиши мне в этот канал — отвечу голосом 🎤"
        )
        self.bot.loop.create_task(self._session_loop(ctx.guild.id))

    @discord.slash_command(description="Выйти из голосового канала")
    async def leave(self, ctx: discord.ApplicationContext):
        session = self.sessions.pop(ctx.guild.id, None)
        if not session:
            await ctx.respond("Меня и так нет в войсе.", ephemeral=True)
            return
        session.active = False
        await session.vc.disconnect()
        await ctx.respond("Вышел. 👋")

    @discord.slash_command(description="Пошутить прямо сейчас")
    async def joke(self, ctx: discord.ApplicationContext):
        session = self.sessions.get(ctx.guild.id)
        if not session:
            await ctx.respond("Сначала позови меня: /join", ephemeral=True)
            return
        await ctx.respond("Щас придумаю...", ephemeral=True)
        await self._tell_joke(session)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        session = self.sessions.get(message.guild.id) if message.guild else None
        if not session or not session.active or message.author.bot:
            return
        if message.channel.id != session.text_channel.id:
            return
        content = message.clean_content.strip()
        if not content:
            return
        session.history.append(
            {"role": "user", "content": f"{message.author.display_name}: {content}"}
        )
        session.fresh_lines += 1
        try:
            reply = await llm.voice_chat(list(session.history))
        except Exception as e:
            await session.text_channel.send(f"⚠️ Мозг не ответил: `{type(e).__name__}: {e}`")
            return
        session.history.append({"role": "assistant", "content": reply})
        await self._speak(session, reply)

    async def _session_loop(self, guild_id: int):
        while True:
            session = self.sessions.get(guild_id)
            if not session or not session.active:
                return
            try:
                await asyncio.sleep(config.JOKE_INTERVAL)
                if session.fresh_lines:
                    session.fresh_lines = 0
                    await self._tell_joke(session)
            except Exception as e:
                print(f"[jester] loop error: {e!r}")
                if time.monotonic() - session.last_err > 60:
                    session.last_err = time.monotonic()
                    try:
                        await session.text_channel.send(f"⚠️ Цикл шуток упал: `{type(e).__name__}: {e}`")
                    except Exception:
                        pass
                await asyncio.sleep(2)

    async def _tell_joke(self, session: JesterSession):
        lines = [m["content"] for m in session.history if m["role"] == "user"]
        context = "\n".join(lines[-10:]) or "Разговор только начался, пошути на любую тему."
        joke = await llm.make_joke(context)
        await self._speak(session, joke)

    async def _speak(self, session: JesterSession, text: str):
        try:
            async with session.speak_lock:
                path = os.path.join(tempfile.gettempdir(), f"jester_{session.vc.guild.id}.mp3")
                await tts.synthesize(text, path)
                while session.vc.is_playing():
                    await asyncio.sleep(0.2)
                done = asyncio.Event()
                source = discord.FFmpegPCMAudio(path)
                session.vc.play(source, after=lambda e: self.bot.loop.call_soon_threadsafe(done.set))
                await done.wait()
        except Exception as e:
            await session.text_channel.send(f"⚠️ Озвучка не сработала: `{type(e).__name__}: {e}`")


def setup(bot):
    bot.add_cog(Jester(bot))
