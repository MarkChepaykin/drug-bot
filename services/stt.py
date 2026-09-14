import asyncio
import io
import wave

import httpx
from groq import Groq, RateLimitError

import config

try:
    import audioop  # встроенный в 3.12; в 3.13 выпилен — тогда шлём как есть
except ImportError:  # pragma: no cover
    audioop = None

_kwargs = {"api_key": config.GROQ_API_KEY}
if config.GROQ_PROXY:
    _kwargs["http_client"] = httpx.Client(proxy=config.GROQ_PROXY, timeout=httpx.Timeout(60.0))
_client = Groq(**_kwargs)

# Whisper любит "додумывать" фразы на тишине/шуме. Отсекаем сегменты, где сама
# модель уверена, что речи не было, или транскрипция крайне неуверенная.
NO_SPEECH_THRESHOLD = 0.6
LOGPROB_THRESHOLD = -1.0
# Whisper внутри всё равно работает на 16кГц моно, а ears пишет 48кГц стерео —
# то есть в Groq каждую реплику уезжало в 6 раз больше байт, чем нужно.
TARGET_RATE = 16000


def _shrink(wav_bytes: bytes) -> bytes:
    """48кГц стерео -> 16кГц моно: тот же звук для распознавания, в 6 раз меньше данных."""
    if audioop is None:
        return wav_bytes
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
            pcm = w.readframes(w.getnframes())
        if width != 2 or (channels == 1 and rate <= TARGET_RATE):
            return wav_bytes
        if channels == 2:
            pcm = audioop.tomono(pcm, width, 0.5, 0.5)
        if rate != TARGET_RATE:
            pcm, _ = audioop.ratecv(pcm, width, 1, rate, TARGET_RATE, None)
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(width)
            w.setframerate(TARGET_RATE)
            w.writeframes(pcm)
        return out.getvalue()
    except Exception as e:
        print(f"[stt] не смог пережать wav ({e!r}) — шлю как есть", flush=True)
        return wav_bytes


async def transcribe(wav_bytes: bytes, hint: str = "") -> str:
    """hint — имена участников войса и кличка бота. Whisper использует его как контекст
    и перестаёт корёжить имена ("Друг" -> "друк", "Саня" -> "сани"), из-за чего бот
    раньше не узнавал обращение к себе и отвечал невпопад."""
    payload = _shrink(wav_bytes)

    def _call():
        return _client.audio.transcriptions.create(
            file=("audio.wav", payload),
            model=config.STT_MODEL,
            language=config.STT_LANGUAGE,
            response_format="verbose_json",
            **({"prompt": hint} if hint else {}),
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
