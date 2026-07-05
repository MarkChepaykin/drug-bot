import asyncio

import httpx
from groq import Groq

import config

_kwargs = {"api_key": config.GROQ_API_KEY}
if config.GROQ_PROXY:
    _kwargs["http_client"] = httpx.Client(proxy=config.GROQ_PROXY, timeout=httpx.Timeout(60.0))
_client = Groq(**_kwargs)


async def transcribe(wav_bytes: bytes) -> str:
    def _call():
        return _client.audio.transcriptions.create(
            file=("audio.wav", wav_bytes),
            model=config.STT_MODEL,
            language=config.STT_LANGUAGE,
            response_format="text",
        )

    result = await asyncio.to_thread(_call)
    text = result if isinstance(result, str) else result.text
    return text.strip()
