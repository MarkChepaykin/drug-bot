import edge_tts

import config


async def synthesize(text: str, path: str) -> str:
    communicate = edge_tts.Communicate(text, config.TTS_VOICE)
    await communicate.save(path)
    return path
