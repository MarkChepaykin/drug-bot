import asyncio

import httpx
from groq import Groq

import config

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
    "Говори как живой человек: разговорно, без канцелярита и пафоса, на чистом русском без "
    "иностранных слов и иероглифов. "
    "Подкол — приправа, а не основное блюдо: шути, когда к месту, а не в каждой реплике. "
    "Рамки: без расизма и реальных угроз."
)

VOICE_CHAT_SYSTEM = PERSONA + (
    " Ты в голосовом канале. Сообщения формата «Имя: текст» — распознанная речь участников "
    "(распознавание может ошибаться — догадывайся по смыслу, не переспрашивай по мелочи). "
    "Отвечай 1-3 предложениями живой устной речи: без эмодзи, разметки, списков и ремарок в скобках."
)

INTERJECT_SYSTEM = PERSONA + (
    " Ты в голосовом канале, следишь за разговором. Никто к тебе не обращался — ты сам решил "
    "вклиниться как участник: развей тему, добавь свою мысль или факт, вспомни, что говорили "
    "раньше, задай интересный вопрос или к месту подколи. Не пересказывай разговор и не "
    "рассказывай анекдоты. 1-2 предложения устной речи, без эмодзи и ремарок."
)

CHAT_SYSTEM = PERSONA + " Отвечай коротко и по делу, на русском."

GREETING_SYSTEM = PERSONA + (
    " Ты только что зашёл в голосовой канал к своей компании. Поздоровайся одной короткой "
    "дерзкой репликой (1-2 предложения), можно сходу подколоть кого-то из присутствующих по имени. "
    "Каждый раз здоровайся по-разному. Только устная речь, без эмодзи и ремарок."
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


async def chat(history: list[dict], system: str = CHAT_SYSTEM) -> str:
    def _call():
        return _client.chat.completions.create(
            model=config.LLM_MODEL,
            temperature=0.8,
            max_tokens=800,
            messages=[{"role": "system", "content": system}] + history,
        )

    resp = await asyncio.to_thread(_call)
    return resp.choices[0].message.content.strip()


async def voice_chat(history: list[dict], notes: str = "") -> str:
    return await chat(history, system=_with_notes(VOICE_CHAT_SYSTEM, notes))


async def interject(history: list[dict], notes: str = "") -> str:
    return await chat(
        history or [{"role": "user", "content": "(в канале пока тихо)"}],
        system=_with_notes(INTERJECT_SYSTEM, notes),
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
