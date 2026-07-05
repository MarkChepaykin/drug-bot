import asyncio
import os
import random
import tempfile
import time

import discord
from discord.ext import commands
from discord.sinks import WaveSink

import config
from services import stt, llm, tts

# Аудио короче этого (PCM 48кГц стерео 16 бит ≈ 192 КБ/сек) считаем тишиной/шумом и пропускаем.
MIN_AUDIO_BYTES = 60_000


class JesterSession:
    def __init__(self, voice_client, text_channel):
        self.vc = voice_client
        self.text_channel = text_channel
        self.transcript: list[str] = []
        self.last_joke = time.monotonic()
        self.active = True
        self.record_done: asyncio.Event | None = None

    def add_lines(self, lines: list[str]):
        self.transcript.extend(lines)
        if len(self.transcript) > config.TRANSCRIPT_MAX_LINES:
            self.transcript = self.transcript[-config.TRANSCRIPT_MAX_LINES:]

    def recent_text(self) -> str:
        return "\n".join(self.transcript)


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

    @discord.slash_command(description="Зайти в твой голосовой канал и начать слушать/шутить")
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
            await ctx.followup.send(f"Не смог подключиться к голосу ({type(e).__name__}) — похоже, не пробит UDP-обход для Discord voice.")
            return
        self.sessions[ctx.guild.id] = JesterSession(vc, ctx.channel)
        await ctx.followup.send(f"Зашёл в **{channel.name}**. Слушаю и буду подшучивать 🎤")
        self.bot.loop.create_task(self._session_loop(ctx.guild.id))

    @discord.slash_command(description="Выйти из голосового канала")
    async def leave(self, ctx: discord.ApplicationContext):
        session = self.sessions.pop(ctx.guild.id, None)
        if not session:
            await ctx.respond("Меня и так нет в войсе.", ephemeral=True)
            return
        session.active = False
        if session.vc.recording:
            session.vc.stop_recording()
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
                await self._record_window(session)
                await self._maybe_joke(session)
            except Exception as e:
                print(f"[jester] loop error: {e!r}")
                if time.monotonic() - getattr(session, "last_err", 0) > 60:
                    session.last_err = time.monotonic()
                    try:
                        await session.text_channel.send(f"⚠️ Цикл прослушки упал: `{type(e).__name__}: {e}`")
                    except Exception:
                        pass
                await asyncio.sleep(2)

    async def _record_window(self, session: JesterSession):
        sink = WaveSink()
        session.record_done = asyncio.Event()

        async def on_done(finished_sink, *_):
            try:
                await self._ingest(session, finished_sink)
            finally:
                session.record_done.set()

        session.vc.start_recording(sink, on_done)
        await asyncio.sleep(config.WINDOW_SECONDS)
        if session.vc.recording:
            session.vc.stop_recording()
        await session.record_done.wait()

    async def _ingest(self, session: JesterSession, sink: WaveSink):
        lines = []
        for user_id, audio in sink.audio_data.items():
            audio.file.seek(0)
            data = audio.file.read()
            if len(data) < MIN_AUDIO_BYTES:
                continue
            text = await stt.transcribe(data)
            if not text:
                continue
            user = self.bot.get_user(user_id)
            name = user.display_name if user else str(user_id)
            lines.append(f"{name}: {text}")
        if lines:
            session.add_lines(lines)
            print(f"[jester] +{len(lines)} реплик")

    async def _maybe_joke(self, session: JesterSession):
        # ВРЕМЕННО (дебаг): шутить после каждого окна, без таймера/рандома/транскрипта
        await self._tell_joke(session)

    async def _tell_joke(self, session: JesterSession):
        joke = await llm.make_joke(session.recent_text() or "Разговор только начался.")
        session.last_joke = time.monotonic()
        await session.text_channel.send(f"🤡 {joke}")
        await self._speak(session, joke)

    async def _speak(self, session: JesterSession, text: str):
        try:
            path = os.path.join(tempfile.gettempdir(), f"jester_{session.vc.guild.id}.mp3")
            await tts.synthesize(text, path)
            if session.vc.recording:
                session.vc.stop_recording()
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
