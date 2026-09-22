import asyncio
import os
import re
import time

import edge_tts
import httpx

import config

# Пресеты голосов: базовый edge-tts голос + сдвиг тона/скорости для мемности.
# Голос — только звучание. Персона всегда одна: Друг.
# rate ускоряет речь БЕЗ изменения тона (нейросеть переозвучивает, а не растягивает),
# поэтому дефолт — родной русский голос, ускоренный, без питч-сдвига (не «растянуто»).
VOICES = {
    # Настоящий Максим: edge-tts база -> RVC-модель MaximBot на Modal (GPU). Если Modal
    # не сконфигурирован/недоступен — авто-фоллбэк на «Обычный».
    "Максим 🎙️": {"engine": "rvc"},
    "Обычный": {"voice": "ru-RU-DmitryNeural", "rate": "+18%"},
    "Пискля 🐿️": {"voice": "ru-RU-DmitryNeural", "rate": "+30%", "pitch": "+45Hz"},
    "Демон 😈": {"voice": "ru-RU-DmitryNeural", "rate": "+8%", "pitch": "-40Hz"},
    "Бас 🗿": {"voice": "ru-RU-DmitryNeural", "rate": "+10%", "pitch": "-22Hz"},
    "Американец 🇺🇸": {"voice": "en-US-AndrewMultilingualNeural", "rate": "+12%"},
    "Немец 🍺": {"voice": "de-DE-FlorianMultilingualNeural", "rate": "+10%"},
    "Француз 🥖": {"voice": "fr-FR-RemyMultilingualNeural", "rate": "+10%"},
    "Робот 🤖": {"engine": "espeak", "speed": "140", "pitch": "50"},
}

# Максим — только если RVC реально настроен. Иначе он молча падал в espeak-робота,
# и компания при каждом старте слышала робота вместо нормального голоса.
DEFAULT_VOICE_KEY = "Максим 🎙️" if config.RVC_URL else "Обычный"

PREVIEWS = {
    "Максим 🎙️": "Это Максим. Донаты и ваши шутки читаю с одинаковым презрением.",
    "Обычный": "Так, вернул нормальный голос. Все выдохнули.",
    "Пискля 🐿️": "А вот так я звучу, когда вы опять что-то сломали.",
    "Демон 😈": "Таким голосом я буду объявлять, кто сегодня играл хуже всех.",
    "Бас 🗿": "Солидный голос. Жаль, компания несолидная.",
    "Американец 🇺🇸": "Хэллоу, парни. Теперь я как будто из Техаса, смиритесь.",
    "Немец 🍺": "Заговорил как немецкий инженер. Порядка в вашем разговоре всё равно не прибавится.",
    "Француз 🥖": "Уи, теперь я звучу дорого. В отличие от ваших шуток.",
    "Робот 🤖": "Теперь я звучу как робот из двухтысячных. Сопротивление бесполезно.",
}


async def _rvc_synth(text: str, path: str) -> bool:
    """Настоящий Максим через Modal (GPU). True — записал wav в path, False — не вышло."""
    if not config.RVC_URL:
        return False
    try:
        async with httpx.AsyncClient(timeout=45.0) as c:
            r = await c.post(config.RVC_URL, json={"text": text, "token": config.RVC_TOKEN})
        if r.status_code != 200 or len(r.content) < 500:
            print(f"[tts] rvc bad response: {r.status_code} len={len(r.content)}", flush=True)
            return False
        with open(path, "wb") as f:
            f.write(r.content)
        return True
    except Exception as e:
        print(f"[tts] rvc error: {e!r}", flush=True)
        return False


# Modal гасит GPU-контейнер через scaledown_window (сейчас 2 мин) после последнего
# запроса. Греем не только при /join, но и на каждую услышанную реплику: иначе первая
# фраза после любой паузы в разговоре ловила холодный старт на десятки секунд —
# со стороны это и есть «бот жутко тормозит».
WARM_EVERY = 45.0
_last_warm = 0.0


async def warm(force: bool = False) -> None:
    """Разбудить/удержать GPU Modal. Зовётся часто — лишние пинги режет троттлинг."""
    global _last_warm
    if not config.RVC_WARM:
        return
    now = time.monotonic()
    if not force and now - _last_warm < WARM_EVERY:
        return
    _last_warm = now
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            await c.get(config.RVC_WARM)
    except Exception:
        pass


# Голос, которым договариваем, если основной пресет не отдал звук.
FALLBACK_VOICE = "ru-RU-SvetlanaNeural"
# Меньше этого размера mp3 не бывает — значит, синтез вернул пустоту.
MIN_AUDIO_BYTES = 256


def speakable(text: str) -> bool:
    """Есть ли что озвучивать. На реплике из одних эмодзи/скобок edge-tts отвечает
    NoAudioReceived — это не сбой, просто озвучивать нечего."""
    return bool(re.search(r"[0-9A-Za-zЀ-ӿ]", text or ""))


async def _edge_synth(text: str, path: str, preset: dict) -> None:
    communicate = edge_tts.Communicate(
        text,
        preset["voice"],
        rate=preset.get("rate", "+0%"),
        pitch=preset.get("pitch", "+0Hz"),
    )
    await communicate.save(path)
    if not os.path.isfile(path) or os.path.getsize(path) < MIN_AUDIO_BYTES:
        raise RuntimeError("edge-tts вернул пустой файл")


async def synthesize(text: str, path: str, voice_key: str | None = None) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    preset = VOICES.get(voice_key) or {"voice": config.TTS_VOICE}
    if preset.get("engine") == "rvc":
        if await _rvc_synth(text, path):
            return path
        preset = VOICES["Обычный"]  # Modal недоступен — говорим обычным голосом, а не роботом
    if preset.get("engine") == "espeak":
        proc = await asyncio.create_subprocess_exec(
            "espeak-ng", "-v", preset.get("lang", "ru"),
            "-s", str(preset.get("speed", "160")),
            "-p", str(preset.get("pitch", "40")),
            "-w", path, text,
        )
        await proc.wait()
        return path
    # Сервис Microsoft регулярно отвечает NoAudioReceived: то сам сбоит, то давится
    # сдвигом тона/скорости. Раньше любая такая осечка = бот молча проглотил реплику,
    # поэтому пробуем ещё раз, потом без эффектов, потом другим голосом.
    attempts = [
        preset,
        preset,
        {**preset, "rate": "+0%", "pitch": "+0Hz"},
        {"voice": FALLBACK_VOICE},
    ]
    last: Exception | None = None
    for i, attempt in enumerate(attempts):
        try:
            await _edge_synth(text, path, attempt)
            if i:
                print(f"[tts] озвучил с {i + 1}-й попытки ({attempt.get('voice')})", flush=True)
            return path
        except Exception as e:
            last = e
            print(f"[tts] попытка {i + 1} не дала звука: {e!r}", flush=True)
            await asyncio.sleep(0.4)
    raise last
