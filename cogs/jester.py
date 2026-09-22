import asyncio
import difflib
import os
import random
import re
import shutil
import tempfile
import time
import uuid
from collections import deque

import discord
from discord.ext import commands

from services import ears, llm, memory, music, soundboard, stt, tts, voiceclips

# Голосом (соединение, приём и проигрывание) управляет Node-сервис ears
# (discord.js + DAVE E2EE). Python — мозг: STT, персона, решения когда говорить.

# Сколько секунд тишины ждать перед ответом в диалоге 1:1 — даёт человеку закончить
# мысль, а не отвечать на каждый обрывок фразы (речь режется на куски по паузам).
# Продлевается в реальном времени сигналом /speaking, так что можно держать коротким.
# Отсчёт идёт от конца речи, а ears до этого уже отмолчал свои 1.1с тишины (AfterSilence) —
# человек ждёт сумму. Резать AfterSilence нельзя: чем он меньше, тем на больше кусков
# режется фраза и тем быстрее выбирается лимит whisper (20 запросов в минуту на free).
# Поэтому экономим здесь: 0.9с — минимум, на котором бот ещё не перебивает.
TURN_GAP = 0.9
# Сколько секунд тишины ждать перед репликой, когда говорят несколько человек.
GROUP_GAP = 5
# В группе (несколько активных) бот вклинивается РЕДКО: не на каждую паузу и не чаще
# раза в N секунд — чтобы при куче народу не тараторил, а вставлял реплику изредка.
GROUP_INTERJECT_CHANCE = 0.3
GROUP_INTERJECT_COOLDOWN = 35
# Автор считается активным участником, если говорил/писал в последние N секунд.
ACTIVE_WINDOW = 60
# Сколько сверх паузы бот готов ждать тишины, прежде чем ответить всё равно.
MAX_EXTRA_WAIT = 2.5
# Сколько последних звуков не предлагать модели и не играть повторно.
RECENT_SOUNDS = 6
# Пока в войсе идёт живой разговор, держим GPU Modal (голос Максима) тёплым: он гаснет
# через две минуты простоя, а холодный старт — это десятки секунд тишины на первой же
# фразе после паузы. Греем, пока с последней распознанной реплики прошло меньше N секунд,
# чтобы не жечь GPU-кредиты, когда компания просто молча слушает музыку.
WARM_WINDOW = 600
WARM_EVERY = 45
# Сколько секунд после своей реплики бот считает входящую речь возможным эхом
# (у людей нет наушников — его же голос возвращается в их микрофоны).
ECHO_WINDOW = 3.0
# Насколько распознанное должно совпасть со сказанным ботом, чтобы счесть это эхом.
ECHO_MATCH = 0.5
# Мусорные фразы Whisper на шуме/тишине.
STT_JUNK = (
    "субтитр", "продолжение следует", "спасибо за просмотр", "dimatorzok",
    "подпишись", "подписывайтесь", "ставьте лайк", "лайк и подписка",
    "до новых встреч", "спасибо за внимание", "редактор субтитров", "корректор",
)
# Если новая реплика бота почти совпадает с одной из недавних — не повторяемся вслух.
REPEAT_SIMILARITY = 0.78

# Голосовые команды музыки: "Друг, включи <трек>", "пропусти", "выключи музыку" и т.д.
# Порядок проверки важен — специфичные паттерны идут раньше общего PLAY_RE.
SLEEP_TIMER_RE = re.compile(
    r"\b(?:выключи|останови)\b.*\bмузык\w*.*?через\s+(\d+)\s*(минут\w*|час\w*)", re.IGNORECASE
)
LEAVE_RE = re.compile(
    r"\b(?:выйди|уйди|свали|отключись|отвались|исчезни)\b.{0,15}\b(?:войс\w*|канал\w*|чат\w*)\b"
    r"|\bпокинь\s+(?:войс\w*|канал\w*|чат\w*)\b",
    re.IGNORECASE,
)
PAUSE_RE = re.compile(r"\bпауза\b|останови\s+трек|стоп\s+трек", re.IGNORECASE)
RESUME_RE = re.compile(r"\b(?:продолжи|возобнови|плей)\b", re.IGNORECASE)
REPEAT_OFF_RE = re.compile(r"\b(?:выключи|сними|убери)\b.*\bповтор", re.IGNORECASE)
REPEAT_ON_RE = re.compile(r"\b(?:повтори|зацикли|повторяй)\b|на\s+повторе", re.IGNORECASE)
SKIP_RE = re.compile(r"\b(?:скип|пропусти|следующ\w*)\b", re.IGNORECASE)
STOP_RE = re.compile(r"\b(?:выключи|останови|хватит)\b.*\bмузык", re.IGNORECASE)
VOLUME_UP_RE = re.compile(r"\b(?:погромче|громче|прибавь\s+звук|увеличь\s+звук)\b", re.IGNORECASE)
VOLUME_DOWN_RE = re.compile(r"\b(?:потише|тише|убавь\s+звук|уменьши\s+звук)\b", re.IGNORECASE)
NOW_PLAYING_RE = re.compile(r"\bчто\s+(?:за\s+трек|играет|это\s+за\s+песня|это\s+за\s+трек)\b", re.IGNORECASE)
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
# «не то поставил», «давай другой вариант» — переключиться на следующий найденный
# вариант того же запроса, не диктуя его заново. Срабатывает только когда варианты есть
# и музыка играет, иначе ловило бы обычное «да не, не то» из разговора.
WRONG_TRACK_RE = re.compile(
    # голое «не то» ловим только на хвосте реплики: «что-то не то с этим билдом»
    # и «не то чтобы» — обычный разговор, а не просьба переключить трек
    r"\b(?:это\s+)?не\s+т[оа]т?\s*(?:песня|трек|вариант|музыка)?\s*[.!?,]*\s*$"
    r"|\bне\s+т[оа]т?\s+(?:песн\w+|трек\w*|вариант\w*|верси\w+)\b"
    r"|\bперепутал\b|\bдавай\s+другой\b"
    r"|\bдруг(?:ой|ую)\s+(?:вариант\w*|верси\w+|трек)\b",
    re.IGNORECASE,
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
# Сколько последних реплик смотреть, чтобы понять, что разговор о Лиге.
LOL_WINDOW = 6
# Сколько последних реплик реально слать в LLM за раз (экономия токенов free-тарифа Groq;
# более долгая память — через session.notes, которые сжимаются отдельно).
RECENT_TURNS = 14

# Настоящий вопрос (а не трёп) — на него отвечаем по делу и подлиннее, llm.voice_chat(answer=True).
# Режим ответа раньше был один на всё («одна фраза, 5-10 слов»), и на «как сделать X» бот
# физически не мог выдать ничего, кроме отмазки — именно это компания звала «тупит».
QUESTION_RE = re.compile(
    r"\bкак(?:ой|ая|ое|ие|ого|ому)?\b|\bпочему\b|\bзачем\b|\bотчего\b|\bсколько\b"
    r"|\bчто\s+так\w+|\bкто\s+так\w+|\bчто\s+знач\w+|\bчем\s+отлич\w+"
    r"|\bв\s+чём\s+разниц\w+|\bчто\s+лучше\b|\bкак\s+назы\w+"
    r"|\bобъясни\b|\bрасскажи\b|\bподскажи\b|\bпосоветуй\b|\bпосчитай\b"
    r"|\bпереведи\b|\bнапомни\b",
    re.IGNORECASE,
)
# «как бы», «так как», «как дела» — не вопрос, на такое нужна колкость, а не доклад.
NOT_QUESTION_RE = re.compile(
    r"\bкак\s+(?:дела|жизнь|ты|сам|оно|там|бы|раз|будто|то|всегда|обычно)\b"
    r"|\bтак\s+как\b|\bкое[\s-]как\b|\bчто\s+(?:нового|как)\b",
    re.IGNORECASE,
)


def _wants_answer(text: str) -> bool:
    low = text.lower().strip()
    if not QUESTION_RE.search(low):
        return False
    if NOT_QUESTION_RE.search(low) and not low.endswith("?"):
        return False
    # обрывок в два-три слова — обычно криво распознанный кусок фразы, а не вопрос
    return len(low.split()) >= 4 or low.endswith("?")


# Мем-звук в реплике модели: [звук:тег] в начале. Вырезаем и проигрываем вместо чтения вслух.
# Модель пишет тег как придётся: [звук:bruh], [sound:bruh], просто [bruh]. Ловим все формы —
# иначе нераспознанная скобка уходила в озвучку и бот читал вслух «саунд брух».
SOUND_TAG_HEAD = re.compile(r"^\s*\[\s*(?:звук|sound)?\s*:?\s*([a-zа-яё0-9_\-]+)\s*\]\s*", re.IGNORECASE)
# Всё остальное в квадратных скобках вырезаем целиком: вслух его читать нельзя в любом случае.
SOUND_TAG_ANY = re.compile(r"\[[^\]]{0,40}\]")
# Не чаще одного звука раз в N секунд на сессию — общий лимит и на авто-звуки по ключевым
# словам, и на теги [звук:тег] от модели. Раньше кулдаун держал только первый канал, а
# модель могла лепить звук в КАЖДУЮ реплику — отсюда «спамит звуками вместо ответов».
SOUND_COOLDOWN = 45
# Голосовые нарезки людей: изредка вставляем чью-то прошлую фразу его же голосом.
CLIP_CALLBACK_CHANCE = 0.12
CLIP_COOLDOWN = 150
# Копим только внятные короткие фразы (48кГц стерео 16бит = 192000 байт/с).
_BPS = 48000 * 2 * 2
CLIP_MIN_BYTES = int(_BPS * 0.8)
CLIP_MAX_BYTES = int(_BPS * 4.0)


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
        # время последней РАСПОЗНАННОЙ реплики (в отличие от last_msg_time, который двигают
        # и пинги «кто-то шуршит в микрофон») — по нему решаем, греть ли GPU под голос
        self.last_line_time = 0.0
        self.pending: asyncio.Task | None = None
        self.turn_direct = False
        # в накопленных за ход репликах был настоящий вопрос -> отвечаем по делу
        self.turn_answer = False
        self.music_active = False
        self.radio_mode = False
        self.repeat_on = False
        self.played_titles: deque[str] = deque(maxlen=15)
        # Что ещё нашлось по последнему запросу — на случай «не то включил».
        self.track_options: list[dict] = []
        self.sleep_timer_task: asyncio.Task | None = None
        self.last_author = 0
        self.last_clip_time = 0.0
        self.last_sound_time = 0.0
        self.last_interject_time = 0.0
        # Последние сыгранные звуки — чтобы не жать одни и те же (модели их не показываем).
        self.recent_sounds: deque[str] = deque(maxlen=RECENT_SOUNDS)
        # Бот говорит сам / только что договорил, и что именно сказал — для отсечки эха.
        self.bot_speaking = False
        self.bot_quiet_since = 0.0
        self.last_spoken_text = ""


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


class TrackSelect(discord.ui.Select):
    """Список других найденных вариантов под сообщением о треке: поиск иногда берёт
    кавер или не ту версию, и это слышно только вживую — нужен способ переключиться."""

    def __init__(self, cog, session, options: list[dict]):
        self.cog = cog
        self.session = session
        self.cands = options[:5]
        super().__init__(
            placeholder="Не то? выбрать другой вариант",
            options=[discord.SelectOption(label=music.label(c)[:100], value=str(i))
                     for i, c in enumerate(self.cands)],
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        cand = self.cands[int(self.values[0])]
        if not await self.cog._switch_to(self.session, cand):
            await interaction.followup.send("Этот вариант не открылся.", ephemeral=True)


class Jester(commands.Cog):
    # Гостям показываем призыв/выход и управление музыкой — остальное бот делает голосом.
    PUBLIC_COMMANDS = {"join", "leave", "play", "skip", "stop", "pause", "resume", "queue", "repeat"}

    def __init__(self, bot):
        self.bot = bot
        self.sessions: dict[int, JesterSession] = {}
        self.default_voice_key = tts.DEFAULT_VOICE_KEY
        # Прячем служебные команды под право «Управление сервером» (не видны обычным участникам).
        hidden = discord.Permissions(manage_guild=True)
        for cmd in self.__cog_commands__:
            if getattr(cmd, "name", None) not in self.PUBLIC_COMMANDS:
                try:
                    cmd.default_member_permissions = hidden
                except Exception:
                    pass

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
        # заметки о компании переживают выход из войса — бот заходит помня прошлые разговоры
        session.notes = memory.load(ctx.guild.id)
        if session.notes:
            print(f"[jester] поднял заметки о компании ({len(session.notes)} символов)", flush=True)
        self.sessions[ctx.guild.id] = session
        self.bot.loop.create_task(tts.warm(force=True))  # заранее будим GPU Modal под голос Максима
        self.bot.loop.create_task(self._keep_warm(session))
        if greet:
            names = [m.display_name for m in channel.members if not m.bot]
            self.bot.loop.create_task(self._greet(session, names))
        return session

    async def _keep_warm(self, session: JesterSession):
        """Фоновый пинг GPU Modal, пока компания разговаривает. Без него каждая пауза
        дольше двух минут стоила холодного старта на первой же ответной фразе."""
        while session.active:
            await asyncio.sleep(WARM_EVERY)
            if session.last_line_time and time.monotonic() - session.last_line_time < WARM_WINDOW:
                await tts.warm(force=True)

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

    async def _voice_leave(self, session: JesterSession):
        try:
            await self._speak(session, "Лады, ухожу.")
            await asyncio.sleep(1.5)
        except Exception:
            pass
        await self._leave_session(session.guild_id, session, "голосовая команда")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        session = self.sessions.get(member.guild.id)
        if not session or not session.active:
            return
        before_id = getattr(before.channel, "id", None)
        after_id = getattr(after.channel, "id", None)
        if session.voice_channel_id not in (before_id, after_id):
            return
        if after_id == session.voice_channel_id and before_id != session.voice_channel_id:
            # человек только что зашёл — коротко отреагировать
            self.bot.loop.create_task(self._welcome(session, member.display_name))
            return
        channel = self.bot.get_channel(session.voice_channel_id)
        if not channel or any(not m.bot for m in channel.members):
            return
        # все люди вышли — не сидим в пустом канале, отвечая на шум/эхо всю ночь
        await self._leave_session(member.guild.id, session, "канал опустел")

    async def _welcome(self, session: JesterSession, name: str):
        try:
            reply = await llm.welcome(name, session.notes)
        except Exception as e:
            await self._report_error(session, "Мозг не ответил", e)
            return
        session.history.append({"role": "assistant", "content": reply})
        await self._speak(session, reply)

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
            title, options = await self._play_track(session, query)
        except Exception as e:
            # буквальный поиск не сработал — подбираем по смыслу, но честно говорим об этом,
            # а не молча подсовываем другую песню вместо запрошенной
            ok = await self._play_surprise(session, hint=query, note=f"Не нашёл «{query}» —")
            await ctx.followup.send("Включил похожее." if ok else f"Не вышло: `{type(e).__name__}: {e}`")
            return
        await self._announce_track(session, title, options, send=ctx.followup.send)

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

    @discord.slash_command(description="Сколько токенов Groq потрачено сегодня")
    async def tokens(self, ctx: discord.ApplicationContext):
        await ctx.respond(f"📊 За сегодня — {llm.usage_line()}", ephemeral=True)

    @discord.slash_command(description="Пусть вклинится в разговор прямо сейчас")
    async def joke(self, ctx: discord.ApplicationContext):
        session = self.sessions.get(ctx.guild.id)
        if not session:
            await ctx.respond("Сначала позови меня: /join", ephemeral=True)
            return
        await ctx.respond("Ага.", ephemeral=True)
        await self._interject(session)

    @discord.slash_command(description="Проиграть мем-звук (без тега — случайный)")
    async def sb(self, ctx: discord.ApplicationContext, tag: str = ""):
        session = self.sessions.get(ctx.guild.id)
        if not session:
            await ctx.respond("Сначала позови меня в войс: /join", ephemeral=True)
            return
        tag = tag.strip().lower()
        if tag and not soundboard.exists(tag):
            await ctx.respond("Нет такого звука. Список: /sounds", ephemeral=True)
            return
        tag = tag or soundboard.random_tag(tuple(session.recent_sounds))
        if not tag:
            await ctx.respond("Саундборд пуст.", ephemeral=True)
            return
        await self._play_sound(session, tag)
        await ctx.respond(f"🔊 {tag}", ephemeral=True)

    @discord.slash_command(description="Показать доступные мем-звуки")
    async def sounds(self, ctx: discord.ApplicationContext):
        tags = soundboard.all_tags()
        if not tags:
            await ctx.respond("Саундборд пуст.", ephemeral=True)
            return
        await ctx.respond("Звуки (`/sb <тег>`):\n" + ", ".join(tags), ephemeral=True)

    @discord.slash_command(description="Вставить прямо сейчас чью-то записанную фразу его голосом")
    async def clip(self, ctx: discord.ApplicationContext):
        session = self.sessions.get(ctx.guild.id)
        if not session:
            await ctx.respond("Сначала позови меня в войс: /join", ephemeral=True)
            return
        if not voiceclips.has_clips(ctx.guild.id):
            await ctx.respond("Пока нечего вставлять — я ещё не наслушался голосов.", ephemeral=True)
            return
        await ctx.respond("🎙️", ephemeral=True)
        await self._clip_callback(session)

    @discord.slash_command(description="Сколько голосовых нарезок записано по каждому")
    async def clips(self, ctx: discord.ApplicationContext):
        data = voiceclips.counts(ctx.guild.id)
        if not data:
            await ctx.respond("Пока пусто.", ephemeral=True)
            return
        lines = "\n".join(f"— {name}: {n}" for name, n in data.items())
        await ctx.respond("Записано фраз:\n" + lines, ephemeral=True)

    # --- входящие реплики: голос (от ears) и текст (из канала) ---

    def handle_speaking(self, data: dict):
        """Реалтайм-пинг «кто-то говорит» от ears — продлевает ожидание, не даёт боту перебивать."""
        session = self.sessions.get(int(data["guild_id"]))
        if not session or not session.active:
            return
        # Пока бот говорит, микрофоны слышат его самого — такие пинги не продлеваем,
        # иначе бот сам себе бесконечно двигает паузу и отвечает с большой задержкой.
        if session.bot_speaking:
            return
        # ВАЖНО: пинг только продлевает ожидание. В session.authors человек попадает
        # ТОЛЬКО за реально распознанную реплику (_on_line) — иначе при открытых микрофонах
        # любой кашель/шорох делал его «активным», active всегда было >= 2, и бот навсегда
        # уходил в групповой режим (пауза 8с + шанс 30% + кулдаун 35с) = молчал всю сессию.
        session.last_msg_time = time.monotonic()

    def _stt_hint(self, session: JesterSession) -> str:
        """Контекст для Whisper: кличка бота и имена сидящих в войсе. Без него распознавание
        регулярно корёжило и имена, и само обращение «друг» — бот не понимал, что зовут его."""
        channel = self.bot.get_channel(session.voice_channel_id)
        names = [m.display_name for m in channel.members if not m.bot][:8] if channel else []
        hint = "Разговор друзей в Discord. Бота зовут Друг."
        if names:
            hint += " Участники: " + ", ".join(names) + "."
        return hint

    async def handle_utterance(self, data: dict):
        session = self.sessions.get(int(data["guild_id"]))
        path = data.get("path", "")
        # Момент, когда человек ДОГОВОРИЛ (ears уже отдал файл). Дальше идёт распознавание,
        # и отсчитывать паузу-ожидание надо от этой точки, а не от возврата STT.
        spoke_at = time.monotonic()
        try:
            if not session or not session.active:
                return
            # Пока идут STT и мозг, держим GPU Modal тёплым: он гаснет через 2 минуты
            # простоя, и первая фраза после паузы иначе ловит холодный старт.
            self.bot.loop.create_task(tts.warm())
            try:
                wav = open(path, "rb").read()
            except OSError:
                return
            try:
                text = await stt.transcribe(wav, self._stt_hint(session))
            except Exception as e:
                print(f"[jester] stt error: {e!r}")
                return
            text = (text or "").strip()
            if len(text) < 2 or any(j in text.lower() for j in STT_JUNK):
                return
            if self._is_echo(session, text):
                print(f"[jester] эхо своей реплики, пропускаю: {text[:60]}", flush=True)
                return
            user_id = int(data["user_id"])
            member = session.text_channel.guild.get_member(user_id)
            name = member.display_name if member else "Кто-то"
            print(f"[jester] услышал {name}: {text}", flush=True)
            # изолированная нарезка голоса — короткая внятная фраза, чтобы позже вставить
            if CLIP_MIN_BYTES <= len(wav) <= CLIP_MAX_BYTES and 6 <= len(text) <= 120:
                voiceclips.add(session.guild_id, user_id, name, path, text)
            direct = re.search(r"\bдруг", text.lower()) is not None
            await self._on_line(session, user_id, name, text, direct, spoke_at)
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

    async def _play_track(self, session: JesterSession, query: str) -> tuple[str, list[dict]]:
        """Найти и поставить в очередь. Возвращает название и запасные варианты.
        identify — запасной ход для описаний вместо названий («та песня из Аркейна,
        где Экко и Джинкс»): их буквальный поиск не вытягивает."""
        url, title, options = await music.resolve_options(
            query, identify=llm.identify_track, pick=llm.pick_track)
        await ears.music(session.guild_id, url, title)
        session.played_titles.append(title)
        session.track_options = options
        return title, options

    async def _announce_track(self, session: JesterSession, title: str, options: list[dict],
                              send=None, prefix: str = ""):
        send = send or session.text_channel.send
        text = f"{prefix} 🎵 **{title}**".strip()
        if options:
            await send(text, view=discord.ui.View(TrackSelect(self, session, options), timeout=900))
        else:
            await send(text)

    async def _switch_to(self, session: JesterSession, cand: dict) -> bool:
        """Заменить играющий трек другим вариантом того же запроса."""
        try:
            url, title = await music.stream(cand["url"])
            await ears.play_now(session.guild_id, url, title)
        except Exception as e:
            await self._report_error(session, "Не смог переключить трек", e)
            return False
        session.played_titles.append(title)
        session.track_options = [c for c in session.track_options if c["url"] != cand["url"]]
        await self._announce_track(session, title, session.track_options)
        return True

    async def _next_option(self, session: JesterSession) -> bool:
        if not session.track_options:
            await self._speak(session, "Других вариантов нет.")
            return False
        return await self._switch_to(session, session.track_options[0])

    async def _play_link(self, session: JesterSession, link: str):
        """Ссылку можно просто кинуть в чат — включаем её, без слов-команд."""
        try:
            title, options = await self._play_track(session, link)
        except Exception as e:
            await self._report_error(session, "Не включил ссылку", e)
            return
        await self._announce_track(session, title, options)

    async def _play_surprise(self, session: JesterSession, hint: str = "", note: str = "") -> bool:
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
        session.track_options = []  # трек выбрал сам бот — переключать «не то» не на что
        await session.text_channel.send(f"{note} 🎵 **{title}**".strip())
        return True

    async def _sleep_timer(self, session: JesterSession, seconds: float):
        try:
            await asyncio.sleep(seconds)
            session.radio_mode = False
            try:
                await ears.stop_music(session.guild_id)
            except Exception:
                pass
            await session.text_channel.send("🌙 Время вышло — выключаю музыку")
        except asyncio.CancelledError:
            pass

    async def _maybe_music_command(self, session: JesterSession, text: str) -> bool:
        low = text.lower()
        link = music.find_link(text)
        if link:
            await self._play_link(session, link)
            return True
        # «не то» — частая фраза в разговоре, поэтому переключаем, только когда играет
        # найденный по запросу трек и есть на что менять
        if session.music_active and session.track_options and WRONG_TRACK_RE.search(low):
            await self._next_option(session)
            return True
        if LEAVE_RE.search(low):
            self.bot.loop.create_task(self._voice_leave(session))
            return True
        m = SLEEP_TIMER_RE.search(low)
        if m:
            amount, unit = int(m.group(1)), m.group(2)
            seconds = amount * (3600 if unit.startswith("час") else 60)
            if session.sleep_timer_task:
                session.sleep_timer_task.cancel()
            session.sleep_timer_task = self.bot.loop.create_task(self._sleep_timer(session, seconds))
            await session.text_channel.send(f"⏲️ Выключу музыку через {amount} {unit}")
            return True
        if VOLUME_UP_RE.search(low):
            try:
                await ears.set_volume(session.guild_id, 0.2)
            except Exception:
                pass
            return True
        if VOLUME_DOWN_RE.search(low):
            try:
                await ears.set_volume(session.guild_id, -0.2)
            except Exception:
                pass
            return True
        if NOW_PLAYING_RE.search(low):
            try:
                data = await ears.queue(session.guild_id)
            except Exception as e:
                await self._report_error(session, "Не узнал, что играет", e)
                return True
            current = data.get("current")
            await self._speak(session, f"Сейчас играет {current}" if current else "Сейчас ничего не играет")
            return True
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
            title, options = await self._play_track(session, query)
        except Exception as e:
            # запрос не похож на конкретное название ("по кайфу", "что-то бодрое") —
            # буквальный поиск не сработал, подбираем трек по смыслу этой фразы
            print(f"[jester] не нашёл «{query}»: {e!r}", flush=True)
            await self._play_surprise(session, hint=query, note=f"Не нашёл «{query}» —")
            return True
        # в сообщение кладём и распознанный запрос: сразу видно, если STT расслышал
        # не то слово, и не надо гадать, почему играет ерунда
        await self._announce_track(session, title, options, prefix=f"«{query}» →")
        await self._speak(session, f"Включаю {title}")
        return True

    async def _queue_fill(self, session: JesterSession, count: int):
        """Набивает очередь несколькими треками разом — не спрашивать песню на каждый заход.
        Резолвит несколько треков параллельно (не по одному), иначе первый трек ждёт,
        пока не найдутся вообще все, и музыка не играет, пока идёт подбор."""
        ok = 0
        attempts = 0
        max_attempts = count * 3  # часть подсказок не резолвится — с запасом попыток
        state_lock = asyncio.Lock()
        stop = False
        added: list[str] = []

        async def worker():
            nonlocal ok, attempts, stop
            while True:
                async with state_lock:
                    if stop or ok >= count or attempts >= max_attempts:
                        return
                    attempts += 1
                try:
                    suggestion = await llm.suggest_track(session.notes, list(session.played_titles))
                    url, title = await music.resolve(suggestion)
                except Exception:
                    continue
                try:
                    await ears.music(session.guild_id, url, title)
                except Exception as e:
                    async with state_lock:
                        stop = True
                    await self._report_error(session, "Очередь не собралась", e)
                    return
                async with state_lock:
                    session.played_titles.append(title)
                    added.append(title)
                    ok += 1

        await asyncio.gather(*(worker() for _ in range(min(3, count))))
        if added:
            listing = "\n".join(f"— {t}" for t in added)
            await session.text_channel.send(f"🎵 Добавил {ok} треков в очередь:\n{listing}")
        else:
            await session.text_channel.send("⚠️ Не нашёл, что добавить")

    def handle_bot_speaking(self, data: dict):
        """ears сообщает, когда бот говорит сам. Пока он говорит, входящий звук — почти
        наверняка его же голос из чужих колонок, а не реплика человека."""
        session = self.sessions.get(int(data["guild_id"]))
        if not session or not session.active:
            return
        session.bot_speaking = bool(data.get("active"))
        if not session.bot_speaking:
            session.bot_quiet_since = time.monotonic()

    async def handle_music_state(self, data: dict):
        session = self.sessions.get(int(data["guild_id"]))
        if not session or not session.active:
            return
        session.music_active = bool(data.get("active"))
        if not session.music_active and session.radio_mode:
            await self._play_surprise(session)

    async def _on_line(self, session: JesterSession, author_id: int, name: str, text: str, direct: bool,
                       spoke_at: float | None = None):
        now = time.monotonic()
        session.authors[author_id] = now
        session.last_author = author_id
        session.last_line_time = now
        # Пауза-ожидание считается от конца речи, а не от прихода расшифровки: STT занимает
        # секунду-полторы, и раньше она молча прибавлялась к TURN_GAP — отсюда «долго думает».
        # max() защищает от отката назад, если человек уже заговорил снова, пока шло распознавание.
        session.last_msg_time = max(session.last_msg_time, spoke_at or now)
        if await self._maybe_music_command(session, text):
            return
        if session.music_active and not direct:
            # во время трека реагируем только на прямое обращение по имени — иначе
            # велик риск отвечать на подхваченные микрофоном звуки самой песни
            print("[jester] промолчал: играет музыка, а обращения по имени не было", flush=True)
            return
        # мгновенный мем-звук по точной ключевой фразе (редко, с кулдауном)
        if now - session.last_sound_time > SOUND_COOLDOWN:
            tag = soundboard.keyword_match(text)
            if tag and tag not in session.recent_sounds:
                session.last_sound_time = now
                self.bot.loop.create_task(self._play_sound(session, tag))
        session.history.append({"role": "user", "content": f"{name}: {text}"})
        session.turn_direct = session.turn_direct or direct
        session.turn_answer = session.turn_answer or _wants_answer(text)
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
        started = time.monotonic()
        try:
            while True:
                active = sum(1 for t in session.authors.values() if time.monotonic() - t < ACTIVE_WINDOW)
                gap = TURN_GAP if (session.turn_direct or session.turn_answer or active <= 1) else GROUP_GAP
                remaining = gap - (time.monotonic() - session.last_msg_time)
                # Потолок: открытый микрофон и эхо музыки шлют «говорит» без пауз, и без него
                # ожидание не кончалось бы никогда — бот молчал бы всю сессию.
                if remaining <= 0 or time.monotonic() - started > gap + MAX_EXTRA_WAIT:
                    break
                await asyncio.sleep(min(remaining, 1))
            if not session.active:
                return
            direct = session.turn_direct
            answer = session.turn_answer
            session.turn_direct = False
            session.turn_answer = False
            active = sum(1 for t in session.authors.values() if time.monotonic() - t < ACTIVE_WINDOW)
            # изредка вместо своей реплики — неожиданно вставить чью-то прошлую фразу
            # его же голосом (не когда обращаются напрямую и не поверх музыки)
            if (not direct and not session.music_active
                    and voiceclips.has_clips(session.guild_id)
                    and time.monotonic() - session.last_clip_time > CLIP_COOLDOWN
                    and random.random() < CLIP_CALLBACK_CHANCE):
                if await self._clip_callback(session):
                    return
            if direct or active <= 1:
                # spoke_end — момент, когда человек договорил; от него и меряем задержку.
                spoke_end, t_wait = session.last_msg_time, time.monotonic()
                try:
                    reply = await llm.voice_chat(list(session.history)[-RECENT_TURNS:], session.notes,
                                                 tuple(session.recent_sounds), answer=answer,
                                                 allow_sound=self._sound_ready(session),
                                                 lol=self._lol_talk(session))
                except Exception as e:
                    await self._report_error(session, "Мозг не ответил", e)
                    return
                if self._too_similar(session, reply):
                    return
                notice = llm.pop_notice()
                if notice:
                    await session.text_channel.send(f"⚠️ {notice}")
                t_llm = time.monotonic()
                session.history.append({"role": "assistant", "content": reply})
                await self._speak(session, reply)
                t_end = time.monotonic()
                print(f"[jester] ответ{' по делу' if answer else ''} за {t_end - spoke_end:.1f}с "
                      f"(пауза+stt {t_wait - spoke_end:.1f} / llm {t_llm - t_wait:.1f}"
                      f" / озвучка {t_end - t_llm:.1f})", flush=True)
            else:
                now = time.monotonic()
                # много активных — чаще молчим: не на каждую групповую паузу и с кулдауном
                if (now - session.last_interject_time < GROUP_INTERJECT_COOLDOWN
                        or random.random() > GROUP_INTERJECT_CHANCE):
                    print(f"[jester] промолчал: групповой режим, активных {active}", flush=True)
                    return
                session.last_interject_time = now
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
            memory.save(session.guild_id, session.notes)
            print(f"[jester] заметки обновлены ({len(session.notes)} символов)", flush=True)
        except Exception as e:
            print(f"[jester] summarize error: {e!r}")

    def _lol_talk(self, session: JesterSession) -> bool:
        """Говорят ли о Лиге прямо сейчас — знание игры подмешиваем в промпт только тогда,
        иначе это лишние ~110 токенов входа в каждом разговоре про работу и машины.
        Смотрим несколько последних реплик: «а что собирать?» идёт уже без слова «лига»."""
        recent = " ".join(m["content"] for m in list(session.history)[-LOL_WINDOW:])
        return llm.mentions_lol(recent)

    def _sound_ready(self, session: JesterSession) -> bool:
        """Звук на кулдауне всё равно будет выброшен в _speak, а меню звуков — это 240
        токенов входа в каждом запросе. Не предлагаем то, что не сыграет."""
        return time.monotonic() - session.last_sound_time >= SOUND_COOLDOWN

    async def _interject(self, session: JesterSession):
        reply = await llm.interject(list(session.history)[-RECENT_TURNS:], session.notes,
                                    tuple(session.recent_sounds),
                                    allow_sound=self._sound_ready(session),
                                    lol=self._lol_talk(session))
        if self._too_similar(session, reply):
            return
        session.history.append({"role": "assistant", "content": reply})
        await self._speak(session, reply)

    def _is_echo(self, session: JesterSession, text: str) -> bool:
        """Свой голос вернулся через чужой микрофон и распознался как чужая реплика.
        Раньше такое попадало в историю, и бот спорил сам с собой — отсюда «несёт хуйню».
        Проверяем только сразу после своей реплики и по совпадению с ней, чтобы не
        глушить живого человека, который говорит поверх бота."""
        if not session.last_spoken_text or len(text) < 6:
            return False
        if not session.bot_speaking and time.monotonic() - session.bot_quiet_since > ECHO_WINDOW:
            return False
        a, b = text.lower(), session.last_spoken_text.lower()
        m = difflib.SequenceMatcher(None, a, b).find_longest_match(0, len(a), 0, len(b))
        return m.size >= max(10, len(a) * ECHO_MATCH)

    def _too_similar(self, session: JesterSession, text: str) -> bool:
        recent = [m["content"] for m in list(session.history)[-6:] if m["role"] == "assistant"]
        return any(
            difflib.SequenceMatcher(None, text.lower(), r.lower()).ratio() > REPEAT_SIMILARITY
            for r in recent
        )

    async def _speak(self, session: JesterSession, text: str):
        # Модель могла поставить в начало мем-звук [звук:тег] — вырезаем и проигрываем его,
        # а любые случайные теги внутри убираем, чтобы не читать вслух скобки.
        tag = None
        m = SOUND_TAG_HEAD.match(text)
        if m:
            tag = m.group(1).lower()
            text = text[m.end():]
        text = SOUND_TAG_ANY.sub("", text).strip()
        try:
            if tag and not soundboard.exists(tag):
                tag = None
            # Звук просят слишком часто или он только что играл — выкидываем его, реплика
            # уходит словами. Если слов нет вообще (модель ответила одним тегом), звук
            # оставляем: молчание вместо ответа хуже лишнего звука.
            if (tag and text and (tag in session.recent_sounds
                                  or time.monotonic() - session.last_sound_time < SOUND_COOLDOWN)):
                print(f"[jester] звук {tag} не играю: кулдаун или повтор", flush=True)
                tag = None
            if tag:
                await self._play_sound(session, tag)
            # реплика из одних эмодзи/скобок — озвучивать нечего, edge-tts на такой
            # отвечает NoAudioReceived, и это выглядело как сбой озвучки
            if text and tts.speakable(text):
                session.last_spoken_text = text
                path = os.path.join(tempfile.gettempdir(), f"speak_{uuid.uuid4().hex}.mp3")
                await tts.synthesize(text, path, session.voice_key)
                await ears.play(session.guild_id, path)
        except Exception as e:
            await self._report_error(session, "Озвучка не сработала", e)

    async def _play_asset(self, session: JesterSession, src_path: str):
        """Проиграть готовый файл (звук/нарезку), не трогая оригинал — ears удаляет то, что играет,
        поэтому отдаём ему одноразовую копию."""
        ext = os.path.splitext(src_path)[1] or ".mp3"
        tmp = os.path.join(tempfile.gettempdir(), f"asset_{uuid.uuid4().hex}{ext}")
        shutil.copyfile(src_path, tmp)
        await ears.play(session.guild_id, tmp)

    async def _play_sound(self, session: JesterSession, tag: str) -> bool:
        p = soundboard.path(tag)
        if not p:
            return False
        try:
            session.last_sound_time = time.monotonic()
            session.recent_sounds.append(tag)
            await self._play_asset(session, p)
            return True
        except Exception as e:
            await self._report_error(session, "Звук не проиграл", e)
            return False

    async def _clip_callback(self, session: JesterSession) -> bool:
        clip = voiceclips.random_clip(session.guild_id, exclude_user=session.last_author)
        if not clip:
            return False
        session.last_clip_time = time.monotonic()
        print(f"[jester] вставляю нарезку {clip.get('name')}: {clip.get('text')}", flush=True)
        try:
            await self._play_asset(session, clip["path"])
            return True
        except Exception as e:
            await self._report_error(session, "Нарезка не проиграла", e)
            return False


def setup(bot):
    bot.add_cog(Jester(bot))
