import asyncio
import os
import re
import tempfile
import time
import uuid
from collections import deque

import discord
from discord.ext import commands

from services import ears, llm, music, stt, tts

# Голосом (соединение, приём и проигрывание) управляет Node-сервис ears
# (discord.js + DAVE E2EE). Python — мозг: STT, персона, решения когда говорить.

# Сколько секунд тишины ждать перед ответом в диалоге 1:1 — даёт человеку закончить
# мысль, а не отвечать на каждый обрывок фразы (речь режется на куски по паузам).
TURN_GAP = 1.8
# Сколько секунд тишины ждать перед репликой, когда говорят несколько человек.
GROUP_GAP = 8
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
        self.history = deque(maxlen=40)
        self.notes = ""
        self.lines_since_sum = 0
        self.authors: dict[int, float] = {}
        self.last_msg_time = 0.0
        self.pending: asyncio.Task | None = None
        self.turn_direct = False


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

    async def _ensure_session(self, ctx, greet: bool = True):
        """Сессия есть — вернуть; нет — подключиться к войсу автора. None при неудаче (ответ уже отправлен)."""
        session = self.sessions.get(ctx.guild.id)
        if session:
            return session
        if not ctx.author.voice:
            await ctx.followup.send("Ты не в голосовом канале.")
            return None
        channel = ctx.author.voice.channel
        try:
            await ears.join(ctx.guild.id, channel.id)
        except Exception as e:
            await ctx.followup.send(f"Не смог подключиться к голосу: `{type(e).__name__}: {e}`")
            return None
        session = JesterSession(ctx.guild.id, ctx.channel, self.default_voice_key)
        self.sessions[ctx.guild.id] = session
        if greet:
            names = [m.display_name for m in channel.members if not m.bot]
            self.bot.loop.create_task(self._greet(session, names))
        return session

    async def _greet(self, session: JesterSession, names: list[str]):
        try:
            hello = await llm.greeting(names, session.notes)
        except Exception as e:
            await session.text_channel.send(f"⚠️ Мозг не ответил: `{type(e).__name__}: {e}`")
            return
        session.history.append({"role": "assistant", "content": hello})
        await self._speak(session, hello)

    @discord.slash_command(description="Зайти в твой голосовой канал и общаться")
    async def join(self, ctx: discord.ApplicationContext):
        if ctx.guild.id in self.sessions:
            await ctx.respond("Я уже тут.", ephemeral=True)
            return
        await ctx.defer()
        session = await self._ensure_session(ctx)
        if session:
            await ctx.followup.send(f"Зашёл 🎤")

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

    @discord.slash_command(description="Включить музыку: название или ссылка (YouTube/Spotify/Яндекс)")
    async def play(self, ctx: discord.ApplicationContext, query: str):
        await ctx.defer()
        session = await self._ensure_session(ctx, greet=False)
        if not session:
            return
        try:
            url, title = await music.resolve(query)
            await ears.music(ctx.guild.id, url, title)
        except Exception as e:
            await ctx.followup.send(f"Не вышло с музыкой: `{type(e).__name__}: {e}`")
            return
        await ctx.followup.send(f"🎵 **{title}**")

    @discord.slash_command(description="Пропустить текущий трек")
    async def skip(self, ctx: discord.ApplicationContext):
        try:
            await ears.skip(ctx.guild.id)
            await ctx.respond("⏭️", ephemeral=True)
        except Exception as e:
            await ctx.respond(f"`{e}`", ephemeral=True)

    @discord.slash_command(description="Остановить музыку и очистить очередь")
    async def stop(self, ctx: discord.ApplicationContext):
        try:
            await ears.stop_music(ctx.guild.id)
            await ctx.respond("⏹️", ephemeral=True)
        except Exception as e:
            await ctx.respond(f"`{e}`", ephemeral=True)

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
        session.turn_direct = session.turn_direct or direct
        session.lines_since_sum += 1
        if session.lines_since_sum >= 25:
            session.lines_since_sum = 0
            self.bot.loop.create_task(self._compact(session))
        # Любая новая реплика (в т.ч. продолжение той же мысли после короткой паузы)
        # перезапускает ожидание — отвечаем только когда человек реально закончил.
        if session.pending and not session.pending.done():
            session.pending.cancel()
        session.pending = self.bot.loop.create_task(self._wait_turn(session))

    async def _wait_turn(self, session: JesterSession):
        try:
            while True:
                active = sum(1 for t in session.authors.values() if time.monotonic() - t < ACTIVE_WINDOW)
                gap = TURN_GAP if (session.turn_direct or active <= 1) else GROUP_GAP
                remaining = gap - (time.monotonic() - session.last_msg_time)
                if remaining <= 0:
                    break
                await asyncio.sleep(min(remaining, 1))
            if not session.active:
                return
            direct = session.turn_direct
            session.turn_direct = False
            active = sum(1 for t in session.authors.values() if time.monotonic() - t < ACTIVE_WINDOW)
            if direct or active <= 1:
                try:
                    reply = await llm.voice_chat(list(session.history), session.notes)
                except Exception as e:
                    await session.text_channel.send(f"⚠️ Мозг не ответил: `{type(e).__name__}: {e}`")
                    return
                session.history.append({"role": "assistant", "content": reply})
                await self._speak(session, reply)
            else:
                await self._interject(session)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[jester] turn-wait error: {e!r}")

    async def _compact(self, session: JesterSession):
        """Сжимает разговор в долгие заметки о компании."""
        lines = [m["content"] for m in list(session.history) if m["role"] == "user"]
        try:
            session.notes = await llm.summarize(session.notes, lines)
            print(f"[jester] заметки обновлены ({len(session.notes)} символов)", flush=True)
        except Exception as e:
            print(f"[jester] summarize error: {e!r}")

    async def _interject(self, session: JesterSession):
        reply = await llm.interject(list(session.history), session.notes)
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
