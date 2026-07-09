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
# Продлевается в реальном времени сигналом /speaking, так что можно держать коротким.
TURN_GAP = 1.1
# Сколько секунд тишины ждать перед репликой, когда говорят несколько человек.
GROUP_GAP = 7
# Автор считается активным участником, если говорил/писал в последние N секунд.
ACTIVE_WINDOW = 60
# Мусорные фразы Whisper на шуме/тишине.
STT_JUNK = ("субтитр", "продолжение следует", "спасибо за просмотр", "dimatorzok")

# Голосовые команды музыки: "Друг, включи <трек>", "пропусти", "выключи музыку" и т.д.
# Порядок проверки важен — специфичные паттерны идут раньше общего PLAY_RE.
PAUSE_RE = re.compile(r"\bпауза\b|останови\s+трек|стоп\s+трек", re.IGNORECASE)
RESUME_RE = re.compile(r"\b(?:продолжи|возобнови|плей)\b", re.IGNORECASE)
REPEAT_OFF_RE = re.compile(r"\b(?:выключи|сними|убери)\b.*\bповтор", re.IGNORECASE)
REPEAT_ON_RE = re.compile(r"\b(?:повтори|зацикли|повторяй)\b|на\s+повторе", re.IGNORECASE)
SKIP_RE = re.compile(r"\b(?:скип|пропусти|следующ\w*)\b", re.IGNORECASE)
STOP_RE = re.compile(r"\b(?:выключи|останови|хватит)\b.*\bмузык", re.IGNORECASE)
RADIO_RE = re.compile(
    r"\b(?:включи|поставь|запусти|давай|врубай|вруби)\b.{0,15}\b(?:волну|радио|плейлист)\b"
    r"|\b(?:волну|радио|плейлист)\b.{0,15}\b(?:включи|поставь|запусти|давай|врубай|вруби)\b",
    re.IGNORECASE,
)
# "накидай треков", "закинь 5 песен в очередь" — набить очередь пачкой сразу,
# чтобы не просить трек за треком.
QUEUE_FILL_RE = re.compile(
    r"\b(?:накидай|закинь|добавь)\b.{0,20}\b(?:треков|трек|песен|песни)\b", re.IGNORECASE
)
QUEUE_FILL_DEFAULT = 5
QUEUE_FILL_MAX = 8
PLAY_RE = re.compile(
    r"\b(?:включи|поставь|запусти|заведи|врубай|вруби)\b\s*(?:мне\s+)?"
    r"(?:музык[ауи]|песн[юяи]|трек)?\s*(.*)",
    re.IGNORECASE,
)
# "включи что-нибудь" / "поставь любую" — просят сюрприз, а не буквальный поиск этих слов.
GENERIC_QUERY_RE = re.compile(
    r"^(?:что.?(?:-)?нибудь|что\s+угодно|люб(?:ую|ое|ой)|как(?:ую|ое|ой)?.?нибудь)$",
    re.IGNORECASE,
)
# Сколько последних реплик реально слать в LLM за раз (экономия токенов free-тарифа Groq;
# более долгая память — через session.notes, которые сжимаются отдельно).
RECENT_TURNS = 14


class JesterSession:
    def __init__(self, guild_id, text_channel, voice_key, voice_channel_id):
        self.guild_id = guild_id
        self.text_channel = text_channel
        self.voice_key = voice_key
        self.voice_channel_id = voice_channel_id
        self.active = True
        self.last_err = 0.0
        self.history = deque(maxlen=40)
        self.notes = ""
        self.lines_since_sum = 0
        self.authors: dict[int, float] = {}
        self.last_msg_time = 0.0
        self.pending: asyncio.Task | None = None
        self.turn_direct = False
        self.music_active = False
        self.radio_mode = False
        self.repeat_on = False
        self.played_titles: deque[str] = deque(maxlen=15)


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
        session = JesterSession(ctx.guild.id, ctx.channel, self.default_voice_key, channel.id)
        self.sessions[ctx.guild.id] = session
        if greet:
            names = [m.display_name for m in channel.members if not m.bot]
            self.bot.loop.create_task(self._greet(session, names))
        return session

    async def _report_error(self, session: JesterSession, prefix: str, e: Exception):
        """Шлёт ⚠️ в канал, но не чаще раза в минуту — иначе при затяжном сбое (например,
        суточный лимит Groq) канал заваливает одинаковыми сообщениями."""
        now = time.monotonic()
        if now - session.last_err < 60:
            print(f"[jester] {prefix}: {e!r} (подавлено, недавно уже сообщал)", flush=True)
            return
        session.last_err = now
        await session.text_channel.send(f"⚠️ {prefix}: `{type(e).__name__}: {e}`")

    async def _leave_session(self, guild_id: int, session: "JesterSession", reason: str):
        session.active = False
        if session.pending:
            session.pending.cancel()
        try:
            await ears.leave(guild_id)
        except Exception:
            pass
        self.sessions.pop(guild_id, None)
        print(f"[jester] вышел из {guild_id}: {reason}", flush=True)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        session = self.sessions.get(member.guild.id)
        if not session or not session.active:
            return
        channel_ids = {getattr(before.channel, "id", None), getattr(after.channel, "id", None)}
        if session.voice_channel_id not in channel_ids:
            return
        channel = self.bot.get_channel(session.voice_channel_id)
        if not channel or any(not m.bot for m in channel.members):
            return
        # все люди вышли — не сидим в пустом канале, отвечая на шум/эхо всю ночь
        await self._leave_session(member.guild.id, session, "канал опустел")

    async def _greet(self, session: JesterSession, names: list[str]):
        try:
            hello = await llm.greeting(names, session.notes)
        except Exception as e:
            await self._report_error(session, "Мозг не ответил", e)
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
        session = self.sessions.get(ctx.guild.id)
        if not session:
            await ctx.respond("Меня и так нет в войсе.", ephemeral=True)
            return
        await self._leave_session(ctx.guild.id, session, "/leave")
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
        session.played_titles.append(title)
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
        session = self.sessions.get(ctx.guild.id)
        if session:
            session.radio_mode = False
        try:
            await ears.stop_music(ctx.guild.id)
            await ctx.respond("⏹️", ephemeral=True)
        except Exception as e:
            await ctx.respond(f"`{e}`", ephemeral=True)

    @discord.slash_command(description="Поставить музыку на паузу")
    async def pause(self, ctx: discord.ApplicationContext):
        try:
            await ears.pause_music(ctx.guild.id)
            await ctx.respond("⏸️", ephemeral=True)
        except Exception as e:
            await ctx.respond(f"`{e}`", ephemeral=True)

    @discord.slash_command(description="Продолжить воспроизведение после паузы")
    async def resume(self, ctx: discord.ApplicationContext):
        try:
            await ears.resume_music(ctx.guild.id)
            await ctx.respond("▶️", ephemeral=True)
        except Exception as e:
            await ctx.respond(f"`{e}`", ephemeral=True)

    @discord.slash_command(description="Зациклить/расциклить текущий трек")
    async def repeat(self, ctx: discord.ApplicationContext):
        session = self.sessions.get(ctx.guild.id)
        if not session:
            await ctx.respond("Сначала позови меня в войс: /join", ephemeral=True)
            return
        session.repeat_on = not session.repeat_on
        try:
            await ears.set_repeat(ctx.guild.id, session.repeat_on)
        except Exception as e:
            await ctx.respond(f"`{e}`", ephemeral=True)
            return
        await ctx.respond("Повтор: " + ("включён 🔁" if session.repeat_on else "выключен"), ephemeral=True)

    @discord.slash_command(description="Показать очередь треков")
    async def queue(self, ctx: discord.ApplicationContext):
        try:
            data = await ears.queue(ctx.guild.id)
        except Exception as e:
            await ctx.respond(f"`{e}`", ephemeral=True)
            return
        lines = []
        if data.get("current"):
            lines.append(f"Сейчас: **{data['current']}**")
        if data.get("queue"):
            lines.append("Дальше: " + ", ".join(data["queue"]))
        await ctx.respond("\n".join(lines) if lines else "Пусто.", ephemeral=True)

    @discord.slash_command(description="Включить/выключить радио — сам подбирает треки по настроению")
    async def radio(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        session = await self._ensure_session(ctx, greet=False)
        if not session:
            return
        session.radio_mode = not session.radio_mode
        if session.radio_mode and not session.music_active:
            await self._play_surprise(session)
        await ctx.followup.send("Радио: " + ("включено 🎶" if session.radio_mode else "выключено"))

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

    def handle_speaking(self, data: dict):
        """Реалтайм-пинг «кто-то говорит» от ears — продлевает ожидание, не даёт боту перебивать."""
        session = self.sessions.get(int(data["guild_id"]))
        if not session or not session.active:
            return
        now = time.monotonic()
        session.last_msg_time = now
        session.authors[int(data["user_id"])] = now

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

    async def _play_surprise(self, session: JesterSession, hint: str = "") -> bool:
        """Сам подбирает трек по настроению/заметкам о компании — для радио, «включи что-нибудь»
        и нечётких запросов вроде «музыку по кайфу», которые не резолвятся буквально."""
        try:
            suggestion = await llm.suggest_track(session.notes, list(session.played_titles), hint)
            url, title = await music.resolve(suggestion)
            await ears.music(session.guild_id, url, title)
        except Exception as e:
            await self._report_error(session, "Не нашёл, что включить", e)
            return False
        session.played_titles.append(title)
        await session.text_channel.send(f"🎵 **{title}**")
        return True

    async def _maybe_music_command(self, session: JesterSession, text: str) -> bool:
        low = text.lower()
        if PAUSE_RE.search(low):
            try:
                await ears.pause_music(session.guild_id)
            except Exception:
                pass
            return True
        if RESUME_RE.search(low):
            try:
                await ears.resume_music(session.guild_id)
            except Exception:
                pass
            return True
        if REPEAT_OFF_RE.search(low):
            session.repeat_on = False
            try:
                await ears.set_repeat(session.guild_id, False)
            except Exception:
                pass
            return True
        if REPEAT_ON_RE.search(low):
            session.repeat_on = True
            try:
                await ears.set_repeat(session.guild_id, True)
            except Exception:
                pass
            return True
        if SKIP_RE.search(low):
            try:
                await ears.skip(session.guild_id)
            except Exception:
                pass
            return True
        if STOP_RE.search(low):
            session.radio_mode = False
            try:
                await ears.stop_music(session.guild_id)
            except Exception:
                pass
            return True
        if RADIO_RE.search(text):
            session.radio_mode = True
            if not session.music_active:
                await self._play_surprise(session)
            return True
        if QUEUE_FILL_RE.search(low):
            nums = re.findall(r"\d+", text)
            count = min(int(nums[0]), QUEUE_FILL_MAX) if nums else QUEUE_FILL_DEFAULT
            self.bot.loop.create_task(self._queue_fill(session, count))
            return True
        m = PLAY_RE.search(text)
        if not m:
            return False
        query = m.group(1).strip(" .,!?—-")
        if not query or GENERIC_QUERY_RE.match(query):
            await self._play_surprise(session)
            return True
        try:
            url, title = await music.resolve(query)
            await ears.music(session.guild_id, url, title)
        except Exception:
            # запрос не похож на конкретное название ("по кайфу", "что-то бодрое") —
            # буквальный поиск не сработал, подбираем трек по смыслу этой фразы
            await self._play_surprise(session, hint=query)
            return True
        session.played_titles.append(title)
        await session.text_channel.send(f"🎵 **{title}**")
        await self._speak(session, f"Включаю {title}")
        return True

    async def _queue_fill(self, session: JesterSession, count: int):
        """Набивает очередь несколькими треками разом — не спрашивать песню на каждый заход."""
        ok = 0
        for _ in range(count):
            try:
                suggestion = await llm.suggest_track(session.notes, list(session.played_titles))
                url, title = await music.resolve(suggestion)
            except Exception:
                continue
            try:
                await ears.music(session.guild_id, url, title)
            except Exception as e:
                await self._report_error(session, "Очередь не собралась", e)
                return
            session.played_titles.append(title)
            ok += 1
        await session.text_channel.send(f"🎵 Добавил {ok} треков в очередь" if ok else "⚠️ Не нашёл, что добавить")

    async def handle_music_state(self, data: dict):
        session = self.sessions.get(int(data["guild_id"]))
        if not session or not session.active:
            return
        session.music_active = bool(data.get("active"))
        if not session.music_active and session.radio_mode:
            await self._play_surprise(session)

    async def _on_line(self, session: JesterSession, author_id: int, name: str, text: str, direct: bool):
        now = time.monotonic()
        session.authors[author_id] = now
        session.last_msg_time = now
        if await self._maybe_music_command(session, text):
            return
        if session.music_active and not direct:
            # во время трека реагируем только на прямое обращение по имени — иначе
            # велик риск отвечать на подхваченные микрофоном звуки самой песни
            return
        session.history.append({"role": "user", "content": f"{name}: {text}"})
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
                    reply = await llm.voice_chat(list(session.history)[-RECENT_TURNS:], session.notes)
                except Exception as e:
                    await self._report_error(session, "Мозг не ответил", e)
                    return
                session.history.append({"role": "assistant", "content": reply})
                await self._speak(session, reply)
            else:
                try:
                    await self._interject(session)
                except Exception as e:
                    await self._report_error(session, "Мозг не ответил", e)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[jester] turn-wait error: {e!r}")

    async def _compact(self, session: JesterSession):
        """Сжимает разговор в долгие заметки о компании."""
        lines = [m["content"] for m in list(session.history) if m["role"] == "user"][-25:]
        try:
            session.notes = await llm.summarize(session.notes, lines)
            print(f"[jester] заметки обновлены ({len(session.notes)} символов)", flush=True)
        except Exception as e:
            print(f"[jester] summarize error: {e!r}")

    async def _interject(self, session: JesterSession):
        reply = await llm.interject(list(session.history)[-RECENT_TURNS:], session.notes)
        session.history.append({"role": "assistant", "content": reply})
        await self._speak(session, reply)

    async def _speak(self, session: JesterSession, text: str):
        try:
            path = os.path.join(tempfile.gettempdir(), f"speak_{uuid.uuid4().hex}.mp3")
            await tts.synthesize(text, path, session.voice_key)
            await ears.play(session.guild_id, path)
        except Exception as e:
            await self._report_error(session, "Озвучка не сработала", e)


def setup(bot):
    bot.add_cog(Jester(bot))
