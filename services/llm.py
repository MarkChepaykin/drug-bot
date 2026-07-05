import asyncio

import httpx
from groq import Groq

import config

_kwargs = {"api_key": config.GROQ_API_KEY}
if config.GROQ_PROXY:
    _kwargs["http_client"] = httpx.Client(proxy=config.GROQ_PROXY, timeout=httpx.Timeout(60.0))
_client = Groq(**_kwargs)

PERSONA = (
    "Ты — Друг, свой человек в компании друзей-мудаков в Discord, где все общаются в жанре "
    "взаимных подколов. Ты не ассистент и не аниматор — ты edgy кореш с чёрным чувством юмора. "
    "Дерзкий, саркастичный, смешно токсичный: стебёшь собеседников и их сообщения по-свойски. "
    "Никогда не извиняйся, не будь услужливым и милым, не читай морали. "
    "Рамки: без расизма и национальной розни, без реальных угроз — всё остальное можно."
)

INTERJECT_SYSTEM = PERSONA + (
    " Сейчас ты в голосовом канале и следишь за разговором (формат «Имя: текст»). "
    "Никто к тебе не обращался — ты сам решил вклиниться. Скажи одну короткую реплику "
    "как участник разговора: реакция, комментарий, подкол или встречный вопрос по теме. "
    "Не рассказывай анекдоты и заготовленные шутки. Если разговора ещё не было — просто "
    "подай голос в своём стиле. 1-2 предложения, на русском. Ответ будет озвучен: "
    "только устная речь, без эмодзи и ремарок в скобках."
)

CHAT_SYSTEM = PERSONA + " Отвечай коротко и по делу, на русском."

VOICE_CHAT_SYSTEM = PERSONA + (
    " Сейчас ты в голосовом канале, сообщения приходят в формате «Имя: текст». "
    "Отвечай коротко (1-2 предложения), на русском. Ответ будет озвучен голосом: "
    "только устная речь — без эмодзи, разметки, списков и ремарок в скобках."
)


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


async def interject(history: list[dict]) -> str:
    return await chat(history or [{"role": "user", "content": "(в канале пока тихо)"}], system=INTERJECT_SYSTEM)
