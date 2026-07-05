import os

from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_PROXY = os.getenv("GROQ_PROXY", "")
DEBUG_GUILD = os.getenv("DEBUG_GUILD")

LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")
STT_MODEL = os.getenv("STT_MODEL", "whisper-large-v3-turbo")
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "ru")
TTS_VOICE = os.getenv("TTS_VOICE", "ru-RU-DmitryNeural")

