import asyncio

import edge_tts

import config

# Пресеты голосов: базовый edge-tts голос + сдвиг тона/скорости для мемности.
# Голос — только звучание. Персона всегда одна: Друг.
VOICES = {
    "Обычный": {"voice": "ru-RU-DmitryNeural"},
    "Пискля 🐿️": {"voice": "ru-RU-DmitryNeural", "rate": "+25%", "pitch": "+45Hz"},
    "Демон 😈": {"voice": "ru-RU-DmitryNeural", "rate": "-10%", "pitch": "-40Hz"},
    "Бас 🗿": {"voice": "ru-RU-DmitryNeural", "rate": "-5%", "pitch": "-22Hz"},
    "Американец 🇺🇸": {"voice": "en-US-AndrewMultilingualNeural"},
    "Немец 🍺": {"voice": "de-DE-FlorianMultilingualNeural"},
    "Француз 🥖": {"voice": "fr-FR-RemyMultilingualNeural"},
    "Робот 🤖": {"engine": "espeak"},
}

DEFAULT_VOICE_KEY = "Американец 🇺🇸"

PREVIEWS = {
    "Обычный": "Так, вернул нормальный голос. Все выдохнули.",
    "Пискля 🐿️": "А вот так я звучу, когда вы опять что-то сломали.",
    "Демон 😈": "Таким голосом я буду объявлять, кто сегодня играл хуже всех.",
    "Бас 🗿": "Солидный голос. Жаль, компания несолидная.",
    "Американец 🇺🇸": "Хэллоу, парни. Теперь я как будто из Техаса, смиритесь.",
    "Немец 🍺": "Заговорил как немецкий инженер. Порядка в вашем разговоре всё равно не прибавится.",
    "Француз 🥖": "Уи, теперь я звучу дорого. В отличие от ваших шуток.",
    "Робот 🤖": "Теперь я звучу как робот из двухтысячных. Сопротивление бесполезно.",
}


async def synthesize(text: str, path: str, voice_key: str | None = None) -> str:
    preset = VOICES.get(voice_key) or {"voice": config.TTS_VOICE}
    if preset.get("engine") == "espeak":
        proc = await asyncio.create_subprocess_exec(
            "espeak-ng", "-v", "ru", "-s", "140", "-w", path, text
        )
        await proc.wait()
        return path
    communicate = edge_tts.Communicate(
        text,
        preset["voice"],
        rate=preset.get("rate", "+0%"),
        pitch=preset.get("pitch", "+0Hz"),
    )
    await communicate.save(path)
    return path
