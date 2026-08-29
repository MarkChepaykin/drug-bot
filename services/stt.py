import asyncio

import httpx
from groq import Groq, RateLimitError

import config

_kwargs = {"api_key": config.GROQ_API_KEY}
if config.GROQ_PROXY:
    _kwargs["http_client"] = httpx.Client(proxy=config.GROQ_PROXY, timeout=httpx.Timeout(60.0))
_client = Groq(**_kwargs)

# Whisper любит "додумывать" фразы на тишине/шуме. Отсекаем сегменты, где сама
# модель уверена, что речи не было, или транскрипция крайне неуверенная.
NO_SPEECH_THRESHOLD = 0.6
LOGPROB_THRESHOLD = -1.0


async def transcribe(wav_bytes: bytes) -> str:
    def _call():
        return _client.audio.transcriptions.create(
            file=("audio.wav", wav_bytes),
            model=config.STT_MODEL,
            language=config.STT_LANGUAGE,
            response_format="verbose_json",
        )

    try:
        result = await asyncio.to_thread(_call)
    except RateLimitError:
        # Free-тариф Groq даёт whisper всего 20 запросов в минуту на организацию, а живой
        # войс на четверых легко выдаёт больше. Без retry реплика просто терялась, и человеку
        # приходилось повторять — со стороны это выглядит как «бот тупит».
        await asyncio.sleep(3)
        result = await asyncio.to_thread(_call)
    segments = getattr(result, "segments", None)
    if not segments:
        return (getattr(result, "text", "") or "").strip()

    parts = []
    for seg in segments:
        no_speech = seg.get("no_speech_prob", 0.0) if isinstance(seg, dict) else getattr(seg, "no_speech_prob", 0.0)
        logprob = seg.get("avg_logprob", 0.0) if isinstance(seg, dict) else getattr(seg, "avg_logprob", 0.0)
        text = seg.get("text", "") if isinstance(seg, dict) else getattr(seg, "text", "")
        if no_speech >= NO_SPEECH_THRESHOLD or logprob <= LOGPROB_THRESHOLD:
            continue
        parts.append(text.strip())
    return " ".join(p for p in parts if p).strip()
