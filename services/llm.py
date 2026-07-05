import asyncio

import httpx
from groq import Groq

import config

_kwargs = {"api_key": config.GROQ_API_KEY}
if config.GROQ_PROXY:
    _kwargs["http_client"] = httpx.Client(proxy=config.GROQ_PROXY, timeout=httpx.Timeout(60.0))
_client = Groq(**_kwargs)

JESTER_SYSTEM = (
    "Ты — голосовой бот-балагур в Discord. Ты слушаешь разговор людей и периодически "
    "вставляешь одну короткую глупую, но смешную шутку или дружеский подкол по теме разговора. "
    "Шути на русском, максимум 1-2 предложения. Не объясняй шутку, не добавляй мораль, "
    "не используй эмодзи и не пиши ремарки в скобках. Если контекста мало — пошути на общую тему. "
    "Можно по-доброму подколоть собеседников, но без оскорблений и токсичности."
)

CHAT_SYSTEM = (
    "Ты — дружелюбный и остроумный Discord-бот. Отвечай коротко и живо, на русском. "
    "Можешь шутить, но по делу."
)

VOICE_CHAT_SYSTEM = (
    "Ты — голосовой собеседник в Discord по имени Друг. Ты сидишь в голосовом канале и общаешься с людьми. "
    "Сообщения приходят в формате «Имя: текст». Отвечай коротко (1-3 предложения), живо и по-дружески, на русском. "
    "Твой ответ будет озвучен голосом: пиши только устную речь — без эмодзи, разметки, списков и ремарок в скобках."
)


async def make_joke(transcript: str) -> str:
    def _call():
        return _client.chat.completions.create(
            model=config.LLM_MODEL,
            temperature=1.0,
            max_tokens=120,
            messages=[
                {"role": "system", "content": JESTER_SYSTEM},
                {"role": "user", "content": f"Недавний разговор:\n{transcript}\n\nВставь одну короткую шутку."},
            ],
        )

    resp = await asyncio.to_thread(_call)
    return resp.choices[0].message.content.strip()


async def chat(history: list[dict], system: str = CHAT_SYSTEM) -> str:
    def _call():
        return _client.chat.completions.create(
            model=config.LLM_MODEL,
            temperature=0.8,
            max_tokens=300,
            messages=[{"role": "system", "content": system}] + history,
        )

    resp = await asyncio.to_thread(_call)
    return resp.choices[0].message.content.strip()


async def voice_chat(history: list[dict]) -> str:
    return await chat(history, system=VOICE_CHAT_SYSTEM)
