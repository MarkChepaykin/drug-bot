import edge_tts

import config

# Пресеты голосов: базовый edge-tts голос + сдвиг тона/скорости для мемности.
VOICES = {
    "Дмитрий": {"voice": "ru-RU-DmitryNeural"},
    "Бурундук 🐿️": {"voice": "ru-RU-DmitryNeural", "rate": "+25%", "pitch": "+45Hz"},
    "Демон 😈": {"voice": "ru-RU-DmitryNeural", "rate": "-10%", "pitch": "-40Hz"},
    "Иностранец 🌍": {"voice": "en-US-AndrewMultilingualNeural"},
    "Немец 🍺": {"voice": "de-DE-FlorianMultilingualNeural"},
    "Француз 🥖": {"voice": "fr-FR-RemyMultilingualNeural"},
}

DEFAULT_VOICE_KEY = "Дмитрий"

PREVIEWS = {
    "Дмитрий": "Ну чё, собрались. Я Дмитрий, буду вас терпеть.",
    "Бурундук 🐿️": "Погнали-погнали-погнали! Кто тут самый медленный, ты?",
    "Демон 😈": "Я восстал из бездны... и даже там компания была получше.",
    "Иностранец 🌍": "Privet, parni. I am inostranets. Ваш русский хуже моего, da.",
    "Немец 🍺": "Гутен таг. Я Флориан. Посмотрел на ваш орднунг — полный хаос.",
    "Француз 🥖": "Бонжур, месье. Я Реми. Уровень вашего юмора — багет недельной давности.",
}


async def synthesize(text: str, path: str, voice_key: str | None = None) -> str:
    preset = VOICES.get(voice_key) or {"voice": config.TTS_VOICE}
    communicate = edge_tts.Communicate(
        text,
        preset["voice"],
        rate=preset.get("rate", "+0%"),
        pitch=preset.get("pitch", "+0Hz"),
    )
    await communicate.save(path)
    return path
