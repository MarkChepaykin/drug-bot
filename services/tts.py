import edge_tts

import config

# Пресеты голосов: базовый edge-tts голос + сдвиг тона/скорости для мемности.
VOICES = {
    "Дмитрий": {"voice": "ru-RU-DmitryNeural"},
    "Светлана": {"voice": "ru-RU-SvetlanaNeural"},
    "Бурундук 🐿️": {"voice": "ru-RU-SvetlanaNeural", "rate": "+25%", "pitch": "+45Hz"},
    "Демон 😈": {"voice": "ru-RU-DmitryNeural", "rate": "-10%", "pitch": "-40Hz"},
    "Иностранец 🌍": {"voice": "en-US-AndrewMultilingualNeural"},
    "Француженка 🥖": {"voice": "fr-FR-VivienneMultilingualNeural"},
}

DEFAULT_VOICE_KEY = "Дмитрий"

PREVIEWS = {
    "Дмитрий": "Здорово. Я Дмитрий, голос по умолчанию. Надёжный, как батин гараж.",
    "Светлана": "Привет, мальчики. Я Светлана. Буду шутить нежно, но больно.",
    "Бурундук 🐿️": "Привет-привет-привет! Я бурундук на трёх энергетиках, погнали шутить!",
    "Демон 😈": "Я восстал из бездны... чтобы рассказывать вам анекдоты.",
    "Иностранец 🌍": "Privet, druzya! I am inostranets. Говорью русский очень хорошо, da?",
    "Француженка 🥖": "Bonjour! Я Вивьен из Парижа. Немного говорю по-русски, уи.",
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
