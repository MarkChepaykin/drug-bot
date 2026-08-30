import asyncio
import re

import httpx
from groq import Groq, RateLimitError

import config
from services import soundboard

_NAME_PREFIX = re.compile(r"^[A-Za-zА-Яа-яЁё][\w -]{1,20}:\s*")

_kwargs = {"api_key": config.GROQ_API_KEY}
if config.GROQ_PROXY:
    _kwargs["http_client"] = httpx.Client(proxy=config.GROQ_PROXY, timeout=httpx.Timeout(60.0))
_client = Groq(**_kwargs)

PERSONA = (
    "Ты — Друг: токсичный дерзкий кореш этой компании в Discord. По сути ты им друг и на их "
    "стороне, но снаружи — сплошной подъёб.\n"
    "ДЛИНА: одна фраза, 5-10 слов. ОДНО предложение, не два. Короткая колкость в лоб — и всё. "
    "Длинная складная речь = провал, даже если она умная.\n"
    "Отвечай ВСЕГДА и НИКОГДА не отвечай пустотой: даже если говорили не с тобой, вставь свои "
    "пять копеек — это твоя компания и твой разговор.\n"
    "ЗАПРЕЩЕНО: сравнения и метафоры («как ..., только ...», «это вроде ...»), объяснять свою "
    "шутку, пересказывать сказанное, вступления («ну», «да уж», «классика», «о,»), морали и "
    "выводы в конце, перечисления.\n"
    "Юмор: сухой, злой, конкретный — цепляйся за конкретное слово или факт из реплики, а не за "
    "тему вообще. Мат к месту («похуй», «пиздец», «ебанулся») — в плюс, но не через слово. "
    "Никого не одёргивай за мат.\n"
    "Спросили КАК что-то сделать — обязан назвать конкретные шаги, коротко и с подъёбом; "
    "отмазки «гугли», «читай инструкцию» запрещены.\n"
    "Речь распознаётся с ошибками: если реплика — бессвязный обрывок, ехидно переспроси двумя "
    "словами, а не выдумывай смысл.\n"
    "Чистый русский, без иероглифов и иностранщины. Без расизма и реальных угроз.\n"
    "Примеры длины и манеры (не копируй текст):\n"
    "Саня: я вчера три часа в очереди простоял → Три часа стоял? Ты мебель.\n"
    "Лёха: короче я эту хуйню так и не починил → Ожидаемо. Руки под пиво заточены.\n"
    "Гоша: друг а как скрин на винде сделать → Win+Shift+S, гений. Мышкой обведи."
)

def _sound_note(exclude: tuple[str, ...] = ()) -> str:
    """Список звуков собираем на каждый запрос: недавно игравшие в него не попадают,
    иначе модель раз за разом жмёт одни и те же теги из начала списка."""
    menu = soundboard.llm_menu(exclude)
    if not menu:
        return ""
    return (
        " Изредка — не чаще чем в одной реплике из пяти — можешь в САМОМ НАЧАЛЕ поставить "
        "мем-звук строго в формате [звук:тег], он проиграется вслух. Чаще всего звук НЕ нужен: без него реплика бьёт сильнее. Можно и без слов — только [звук:тег]. "
        f"Доступные звуки (тег — когда уместно): {menu}."
    )

_VOICE_CHAT_BASE = PERSONA + (
    " Ты в голосовом канале. Сообщения формата «Имя: текст» — распознанная речь участников "
    "(распознавание может ошибаться и терять слова — догадывайся по смыслу, не переспрашивай "
    "по мелочи и не придирайся к неровностям текста). "
    "Свой ответ пиши БЕЗ «Имя:» в начале — этот формат только во входящих сообщениях, "
    "ты говоришь от себя напрямую. Только устная речь: без эмодзи, разметки, списков и ремарок в скобках."
)

_INTERJECT_BASE = PERSONA + (
    " Ты в голосовом канале, следишь за разговором. Никто к тебе не обращался — ты сам решил "
    "вклиниться как участник: развей тему, добавь свою мысль или факт, вспомни, что говорили "
    "раньше, задай интересный вопрос или к месту подколи. Не пересказывай разговор и не "
    "рассказывай анекдоты. Свой ответ пиши БЕЗ «Имя:» в начале, говори от себя напрямую. "
    "Только устная речь, без эмодзи и ремарок."
)

CHAT_SYSTEM = PERSONA + " Отвечай коротко и по делу, на русском."

GREETING_SYSTEM = PERSONA + (
    " Ты только что зашёл в голосовой канал к своей компании. Поздоровайся одной короткой "
    "дерзкой репликой (1-2 предложения), можно сходу подколоть кого-то из присутствующих по имени. "
    "Каждый раз здоровайся по-разному. Только устная речь, без эмодзи и ремарок."
)

JOIN_SYSTEM = PERSONA + (
    " Ты сидишь в голосовом канале с компанией, кто-то из них только что зашёл. Встреть его "
    "коротко и по-свойски, можно с подколом, каждый раз по-разному. Одна фраза устной речи, "
    "без эмодзи и ремарок."
)

TRACK_SUGGEST_SYSTEM = (
    "Ты подбираешь следующий трек для прослушивания в этой компании друзей — учитывай "
    "настроение и тему недавнего разговора. Ответь СТРОГО в формате «Исполнитель - Название», "
    "один конкретный реально существующий трек, без пояснений, кавычек и лишних слов. "
    "Не повторяй то, что уже играло."
)

SUMMARIZE_SYSTEM = (
    "Ты ведёшь личные заметки о компании друзей по их разговорам. Обнови заметки: объедини "
    "старые с новым куском разговора. Сохраняй факты о людях (интересы, привычки, кто как "
    "играет), их истории, обсуждавшиеся темы и договорённости. Отдельно и явно фиксируй "
    "музыкальные вкусы каждого по имени (что за артистов/жанры называли, что понравилось или "
    "разонравилось из включённого) — это используется для подбора треков. Пиши сжато, по "
    "пунктам, максимум 150 слов. Верни только сами заметки, без вступлений."
)


def _with_notes(system: str, notes: str) -> str:
    if notes:
        return system + f" Твои заметки о компании из прошлых разговоров: {notes}"
    return system


# Обе линейки моделей на Groq думают перед ответом, и думалка тратит тот же max_tokens.
# qwen понимает "none" — думать не надо совсем (и без этого сыпет <think> прямо в реплику);
# gpt-oss "none" не принимает, у него минимум "low".
if config.LLM_MODEL.startswith("qwen/"):
    _EXTRA = {"reasoning_effort": "none"}
elif config.LLM_MODEL.startswith("openai/gpt-oss"):
    _EXTRA = {"reasoning_effort": "low"}
else:
    _EXTRA = {}


async def chat(history: list[dict], system: str = CHAT_SYSTEM, max_tokens: int = 800) -> str:
    def _call():
        return _client.chat.completions.create(
            model=config.LLM_MODEL,
            temperature=0.8,
            max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}] + history,
            **_EXTRA,
        )

    try:
        resp = await asyncio.to_thread(_call)
    except RateLimitError as e:
        if "per day" in str(e) or "TPD" in str(e) or "RPD" in str(e):
            # суточный лимит — retry через 5с бессмысленен, сбросится через минуты/часы
            raise
        # короткий per-minute лимит — обычно отпускает за несколько секунд
        await asyncio.sleep(5)
        resp = await asyncio.to_thread(_call)
    text = (resp.choices[0].message.content or "").strip()
    if not text:
        # Модель иногда молча отдаёт пустой ответ (особенно на групповой трёп без прямого
        # обращения) — в войсе это выглядит как «бот оглох». Пробуем ещё раз.
        resp = await asyncio.to_thread(_call)
        text = (resp.choices[0].message.content or "").strip()
    # иногда модель по инерции копирует формат "Имя: текст" из истории — срезаем
    return _NAME_PREFIX.sub("", text, count=1)


async def voice_chat(history: list[dict], notes: str = "", recent_sounds: tuple[str, ...] = ()) -> str:
    system = _with_notes(_VOICE_CHAT_BASE + _sound_note(recent_sounds), notes)
    return await chat(history, system=system, max_tokens=100)


async def interject(history: list[dict], notes: str = "", recent_sounds: tuple[str, ...] = ()) -> str:
    return await chat(
        history or [{"role": "user", "content": "(в канале пока тихо)"}],
        system=_with_notes(_INTERJECT_BASE + _sound_note(recent_sounds), notes),
        max_tokens=80,
    )


async def greeting(member_names: list[str], notes: str = "") -> str:
    who = ", ".join(member_names) if member_names else "никого, пустой канал"
    return await chat(
        [{"role": "user", "content": f"В канале сидят: {who}. Ты заходишь — поздоровайся."}],
        system=_with_notes(GREETING_SYSTEM, notes),
        max_tokens=80,
    )


async def welcome(name: str, notes: str = "") -> str:
    return await chat(
        [{"role": "user", "content": f"{name} только что зашёл в канал. Отреагируй."}],
        system=_with_notes(JOIN_SYSTEM, notes),
        max_tokens=80,
    )


async def summarize(notes: str, lines: list[str]) -> str:
    content = f"Старые заметки:\n{notes or '—'}\n\nНовый кусок разговора:\n" + "\n".join(lines)
    return await chat([{"role": "user", "content": content}], system=SUMMARIZE_SYSTEM)


async def suggest_track(notes: str, recent_titles: list[str], hint: str = "") -> str:
    recent = ", ".join(recent_titles) if recent_titles else "ничего ещё"
    content = f"Настроение/заметки о компании: {notes or 'пока нет'}. Уже играло: {recent}."
    if hint:
        content += f" Пожелание по треку от собеседника: «{hint}» — учти его при выборе."
    content += " Предложи следующий трек."
    return await chat([{"role": "user", "content": content}], system=TRACK_SUGGEST_SYSTEM, max_tokens=40)
