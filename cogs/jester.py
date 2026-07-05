import asyncio
import os
import tempfile
import time

import discord
from discord.ext import commands

import config
from services import llm, tts

# Прослушка разговора ВЫКЛЮЧЕНА: приём голоса сломан в py-cord 2.8
# (DAVE E2EE, https://github.com/Pycord-Development/pycord/issues/3139).
# Пока бот шутит по таймеру и по /joke, озвучивая через TTS.


class JesterSession:
    def __init__(self, voice_client, text_channel):
        self.vc = voice_client
        self.text_channel = text_channel
        self.active = True
        self.last_err = 0.0


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

    @discord.slash_command(description="Зайти в твой голосовой канал и шутить")
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
        await ctx.followup.send(f"Зашёл в **{channel.name}**. Буду подшучивать 🎤")
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

    async def _session_loop(self, guild_id: int):
        while True:
            session = self.sessions.get(guild_id)
            if not session or not session.active:
                return
            try:
                await asyncio.sleep(config.JOKE_INTERVAL)
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
        joke = await llm.make_joke(
            "Разговор не слышно, придумай короткую шутку на любую тему сам."
        )
        await session.text_channel.send(f"🤡 {joke}")
        await self._speak(session, joke)

    async def _speak(self, session: JesterSession, text: str):
        try:
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
