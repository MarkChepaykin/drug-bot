import asyncio
import os
import re
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


# Сколько секунд тишины ждать перед репликой, когда говорят несколько человек.
QUIET_SECONDS = 8
# Автор считается активным участником, если писал в последние N секунд.
ACTIVE_WINDOW = 60


class JesterSession:
    def __init__(self, voice_client, text_channel, voice_key):
        self.vc = voice_client
        self.text_channel = text_channel
        self.voice_key = voice_key
        self.active = True
        self.last_err = 0.0
        self.history = deque(maxlen=12)
        self.speak_lock = asyncio.Lock()
        self.authors: dict[int, float] = {}
        self.last_msg_time = 0.0
        self.pending: asyncio.Task | None = None


class VoiceSelect(discord.ui.Select):
    def __init__(self, cog, session):
        self.cog = cog
        self.session = session
        options = [
            discord.SelectOption(label=name, default=(name == session.voice_key))
            for name in tts.VOICES
        ]
        super().__init__(placeholder="Каким голосом говорить?", options=options)

    async def callback(self, interaction: discord.Interaction):
        key = self.values[0]
        self.session.voice_key = key
        self.cog.default_voice_key = key
        await interaction.response.send_message(f"Голос: **{key}**", ephemeral=True)
        preview = tts.PREVIEWS.get(key, "Привет, теперь я говорю вот так.")
        await self.cog._speak(self.session, preview)


class Jester(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.sessions: dict[int, JesterSession] = {}
        self.default_voice_key = tts.DEFAULT_VOICE_KEY

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
        self.sessions[ctx.guild.id] = JesterSession(vc, ctx.channel, self.default_voice_key)
        await ctx.followup.send(
            f"Зашёл в **{channel.name}**. Пиши мне в этот канал — отвечу голосом 🎤"
        )

    @discord.slash_command(description="Выйти из голосового канала")
    async def leave(self, ctx: discord.ApplicationContext):
        session = self.sessions.pop(ctx.guild.id, None)
        if not session:
            await ctx.respond("Меня и так нет в войсе.", ephemeral=True)
            return
        session.active = False
        if session.pending:
            session.pending.cancel()
        await session.vc.disconnect()
        await ctx.respond("Вышел. 👋")

    @discord.slash_command(description="Выбрать голос бота (с озвученным превью)")
    async def voice(self, ctx: discord.ApplicationContext):
        session = self.sessions.get(ctx.guild.id)
        if not session:
            await ctx.respond("Сначала позови меня в войс: /join", ephemeral=True)
            return
        view = discord.ui.View(VoiceSelect(self, session), timeout=120)
        await ctx.respond("Выбери голос — я сразу скажу превью:", view=view, ephemeral=True)

    @discord.slash_command(description="Пусть вклинится в разговор прямо сейчас")
    async def joke(self, ctx: discord.ApplicationContext):
        session = self.sessions.get(ctx.guild.id)
        if not session:
            await ctx.respond("Сначала позови меня: /join", ephemeral=True)
            return
        await ctx.respond("Ага.", ephemeral=True)
        await self._interject(session)

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
        now = time.monotonic()
        session.history.append(
            {"role": "user", "content": f"{message.author.display_name}: {content}"}
        )
        session.last_msg_time = now
        session.authors[message.author.id] = now
        active = sum(1 for t in session.authors.values() if now - t < ACTIVE_WINDOW)
        direct = (
            self.bot.user in message.mentions
            or re.search(r"\bдруг\b", content.lower()) is not None
        )
        if direct or active <= 1:
            # обращение или диалог один на один — отвечаем сразу
            if session.pending:
                session.pending.cancel()
                session.pending = None
            try:
                reply = await llm.voice_chat(list(session.history))
            except Exception as e:
                await session.text_channel.send(f"⚠️ Мозг не ответил: `{type(e).__name__}: {e}`")
                return
            session.history.append({"role": "assistant", "content": reply})
            await self._speak(session, reply)
        elif not session.pending or session.pending.done():
            # оживлённый разговор — ждём тишины и вставляем одну реплику
            session.pending = self.bot.loop.create_task(self._wait_quiet(session))

    async def _wait_quiet(self, session: JesterSession):
        try:
            while time.monotonic() - session.last_msg_time < QUIET_SECONDS:
                await asyncio.sleep(1)
            if session.active:
                await self._interject(session)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[jester] quiet-wait error: {e!r}")

    async def _interject(self, session: JesterSession):
        reply = await llm.interject(list(session.history))
        session.history.append({"role": "assistant", "content": reply})
        await self._speak(session, reply)

    async def _speak(self, session: JesterSession, text: str):
        try:
            async with session.speak_lock:
                path = os.path.join(tempfile.gettempdir(), f"jester_{session.vc.guild.id}.mp3")
                await tts.synthesize(text, path, session.voice_key)
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
