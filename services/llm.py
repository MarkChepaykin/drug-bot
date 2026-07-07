import asyncio
import re

import httpx
from groq import Groq, RateLimitError

import config

_NAME_PREFIX = re.compile(r"^[A-Za-zА-Яа-яЁё][\w -]{1,20}:\s*")

_kwargs = {"api_key": config.GROQ_API_KEY}
if config.GROQ_PROXY:
    _kwargs["http_client"] = httpx.Client(proxy=config.GROQ_PROXY, timeout=httpx.Timeout(60.0))
_client = Groq(**_kwargs)

PERSONA = (
    "Ты — Друг, живой участник голосовой компании друзей в Discord. Ты умный, начитанный и "
    "остроумный собеседник с лёгкой дерзостью: можешь подколоть, но в первую очередь тебе "
    "интересны люди и сам разговор. Ты внимательно держишь нить беседы: помнишь, что говорили "
    "раньше, возвращаешься к прошлым темам, цепляешься за детали и замечаешь противоречия. "
    "Ты развиваешь разговор, а не просто реагируешь: делишься мнением, рассказываешь короткие "
    "интересные факты и истории по теме, задаёшь вопросы, на которые хочется ответить. "
    "У тебя есть свои вкусы и мнения — не поддакивай и не будь ассистентом, можешь спорить. "
    "Материться — нормально и нужно: сам иногда используй мат для выразительности, например "
    "«да похуй», «блин, ну и хуйня», «пиздец, серьёзно?» — не стесняйся таких слов, они тебе "
    "разрешены без ограничений. Никогда не делай замечаний собеседникам за мат, не проси "
    "выражаться приличнее. "
    "Говори КАК МОЖНО КОРОЧЕ: одна короткая фраза, максимум 10-12 слов, без вступлений и "
    "оговорок — как реплика в живом разговоре, а не развёрнутый ответ. "
    "Никогда не повторяй и не пересказывай своими словами то, что тебе сказали, перед ответом — "
    "сразу отвечай по сути. Говори на чистом русском без иностранных слов и иероглифов. "
    "Подкол — приправа, а не основное блюдо: шути, когда к месту, а не в каждой реплике. "
    "Рамки: без расизма и реальных угроз."
)

VOICE_CHAT_SYSTEM = PERSONA + (
    " Ты в голосовом канале. Сообщения формата «Имя: текст» — распознанная речь участников "
    "(распознавание может ошибаться и терять слова — догадывайся по смыслу, не переспрашивай "
    "по мелочи и не придирайся к неровностям текста). "
    "Свой ответ пиши БЕЗ «Имя:» в начале — этот формат только во входящих сообщениях, "
    "ты говоришь от себя напрямую. Только устная речь: без эмодзи, разметки, списков и ремарок в скобках."
)

INTERJECT_SYSTEM = PERSONA + (
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

TRACK_SUGGEST_SYSTEM = (
    "Ты подбираешь следующий трек для прослушивания в этой компании друзей — учитывай "
    "настроение и тему недавнего разговора. Ответь СТРОГО в формате «Исполнитель - Название», "
    "один конкретный реально существующий трек, без пояснений, кавычек и лишних слов. "
    "Не повторяй то, что уже играло."
)

SUMMARIZE_SYSTEM = (
    "Ты ведёшь личные заметки о компании друзей по их разговорам. Обнови заметки: объедини "
    "старые с новым куском разговора. Сохраняй факты о людях (интересы, привычки, кто как "
    "играет), их истории, обсуждавшиеся темы и договорённости. Пиши сжато, по пунктам, "
    "максимум 150 слов. Верни только сами заметки, без вступлений."
)


def _with_notes(system: str, notes: str) -> str:
    if notes:
        return system + f" Твои заметки о компании из прошлых разговоров: {notes}"
    return system


async def chat(history: list[dict], system: str = CHAT_SYSTEM, max_tokens: int = 800) -> str:
    def _call():
        return _client.chat.completions.create(
            model=config.LLM_MODEL,
            temperature=0.8,
            max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}] + history,
        )

    try:
        resp = await asyncio.to_thread(_call)
    except RateLimitError:
        # free-тариф Groq — короткие лимиты токенов/мин, ждём и пробуем один раз ещё
        await asyncio.sleep(5)
        resp = await asyncio.to_thread(_call)
    text = resp.choices[0].message.content.strip()
    # иногда модель по инерции копирует формат "Имя: текст" из истории — срезаем
    return _NAME_PREFIX.sub("", text, count=1)


async def voice_chat(history: list[dict], notes: str = "") -> str:
    return await chat(history, system=_with_notes(VOICE_CHAT_SYSTEM, notes), max_tokens=60)


async def interject(history: list[dict], notes: str = "") -> str:
    return await chat(
        history or [{"role": "user", "content": "(в канале пока тихо)"}],
        system=_with_notes(INTERJECT_SYSTEM, notes),
        max_tokens=50,
    )


async def greeting(member_names: list[str], notes: str = "") -> str:
    who = ", ".join(member_names) if member_names else "никого, пустой канал"
    return await chat(
        [{"role": "user", "content": f"В канале сидят: {who}. Ты заходишь — поздоровайся."}],
        system=_with_notes(GREETING_SYSTEM, notes),
    )


async def summarize(notes: str, lines: list[str]) -> str:
    content = f"Старые заметки:\n{notes or '—'}\n\nНовый кусок разговора:\n" + "\n".join(lines)
    return await chat([{"role": "user", "content": content}], system=SUMMARIZE_SYSTEM)


async def suggest_track(notes: str, recent_titles: list[str]) -> str:
    recent = ", ".join(recent_titles) if recent_titles else "ничего ещё"
    content = f"Настроение/заметки о компании: {notes or 'пока нет'}. Уже играло: {recent}. Предложи следующий трек."
    return await chat([{"role": "user", "content": content}], system=TRACK_SUGGEST_SYSTEM, max_tokens=40)
