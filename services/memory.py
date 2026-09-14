"""Долгая память о компании: заметки живут дольше одной посиделки в войсе.

Раньше session.notes собирались за вечер и умирали вместе с сессией (/leave, пустой
канал, рестарт) — на следующий день бот заходил с полной амнезией и не помнил ни имён,
ни историй, ни музыкальных вкусов. Это и есть половина ощущения «он тупой».

Хранилище простое: один JSON на все гильдии. На Render диск эфемерный, поэтому при
редеплое заметки всё же обнулятся — но не при каждом выходе из войса, как было.
"""
import json
import os
import threading

_DIR = os.getenv("BOT_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)
_FILE = os.path.join(_DIR, "notes.json")
_lock = threading.Lock()
_cache: dict[str, str] | None = None


def _read() -> dict[str, str]:
    global _cache
    if _cache is None:
        try:
            with open(_FILE, encoding="utf-8") as f:
                data = json.load(f)
            _cache = {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except FileNotFoundError:
            _cache = {}
        except Exception as e:
            print(f"[memory] заметки не прочитались: {e!r}", flush=True)
            _cache = {}
    return _cache


def load(guild_id: int) -> str:
    with _lock:
        return _read().get(str(guild_id), "")


def save(guild_id: int, notes: str) -> None:
    if not notes:
        return
    with _lock:
        data = _read()
        data[str(guild_id)] = notes
        try:
            os.makedirs(_DIR, exist_ok=True)
            tmp = _FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, _FILE)
        except Exception as e:
            print(f"[memory] заметки не записались: {e!r}", flush=True)
