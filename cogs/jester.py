import asyncio
import os
import re
import tempfile
import time
import uuid
from collections import deque

import discord
from discord.ext import commands

from services import ears, llm, stt, tts

# Голосом (соединение, приём и проигрывание) управляет Node-сервис ears
# (discord.js + DAVE E2EE). Python — мозг: STT, персона, решения когда говорить.

# Сколько секунд тишины ждать перед репликой, когда говорят несколько человек.
QUIET_SECONDS = 8
# Автор считается активным участником, если говорил/писал в последние N секунд.
ACTIVE_WINDOW = 60
# Мусорные фразы Whisper на шуме/тишине.
STT_JUNK = ("субтитр", "продолжение следует", "спасибо за просмотр", "dimatorzok")


class JesterSession:
    def __init__(self, guild_id, text_channel, voice_key):
        self.guild_id = guild_id
        self.text_channel = text_channel
        self.voice_key = voice_key
        self.active = True
        self.last_err = 0.0
        self.history = deque(maxlen=16)
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

    @discord.slash_command(description="Зайти в твой голосовой канал и общаться")
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
            await ears.join(ctx.guild.id, channel.id)
        except Exception as e:
            await ctx.followup.send(f"Не смог подключиться к голосу: `{type(e).__name__}: {e}`")
            return
        session = JesterSession(ctx.guild.id, ctx.channel, self.default_voice_key)
        self.sessions[ctx.guild.id] = session
        await ctx.followup.send(f"Зашёл в **{channel.name}** 🎤")
        names = [m.display_name for m in channel.members if not m.bot]
        try:
            hello = await llm.greeting(names)
        except Exception as e:
            await session.text_channel.send(f"⚠️ Мозг не ответил: `{type(e).__name__}: {e}`")
            return
        session.history.append({"role": "assistant", "content": hello})
        await self._speak(session, hello)

    @discord.slash_command(description="Выйти из голосового канала")
    async def leave(self, ctx: discord.ApplicationContext):
        session = self.sessions.pop(ctx.guild.id, None)
        if not session:
            await ctx.respond("Меня и так нет в войсе.", ephemeral=True)
            return
        session.active = False
        if session.pending:
            session.pending.cancel()
        try:
            await ears.leave(ctx.guild.id)
        except Exception:
            pass
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

    # --- входящие реплики: голос (от ears) и текст (из канала) ---

    async def handle_utterance(self, data: dict):
        session = self.sessions.get(int(data["guild_id"]))
        path = data.get("path", "")
        try:
            if not session or not session.active:
                return
            try:
                wav = open(path, "rb").read()
            except OSError:
                return
            try:
                text = await stt.transcribe(wav)
            except Exception as e:
                print(f"[jester] stt error: {e!r}")
                return
            text = (text or "").strip()
            if len(text) < 2 or any(j in text.lower() for j in STT_JUNK):
                return
            user_id = int(data["user_id"])
            member = session.text_channel.guild.get_member(user_id)
            name = member.display_name if member else "Кто-то"
            print(f"[jester] услышал {name}: {text}", flush=True)
            direct = re.search(r"\bдруг", text.lower()) is not None
            await self._on_line(session, user_id, name, text, direct)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

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
        direct = (
            self.bot.user in message.mentions
            or re.search(r"\bдруг\b", content.lower()) is not None
        )
        await self._on_line(session, message.author.id, message.author.display_name, content, direct)

    async def _on_line(self, session: JesterSession, author_id: int, name: str, text: str, direct: bool):
        now = time.monotonic()
        session.history.append({"role": "user", "content": f"{name}: {text}"})
        session.last_msg_time = now
        session.authors[author_id] = now
        active = sum(1 for t in session.authors.values() if now - t < ACTIVE_WINDOW)
        if direct or active <= 1:
            # обращение или разговор один на один — отвечаем сразу
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
            path = os.path.join(tempfile.gettempdir(), f"speak_{uuid.uuid4().hex}.mp3")
            await tts.synthesize(text, path, session.voice_key)
            await ears.play(session.guild_id, path)
        except Exception as e:
            await session.text_channel.send(f"⚠️ Озвучка не сработала: `{type(e).__name__}: {e}`")


def setup(bot):
    bot.add_cog(Jester(bot))
