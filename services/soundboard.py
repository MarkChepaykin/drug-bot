"""Мемный саундборд: бот проигрывает короткие звуки в тему.

Звуки лежат в sounds/*.mp3, метаданные — в sounds/manifest.json. Проигрывает их
тот же путь, что и речь (ears.play), поэтому здесь только выбор файла.
"""
import json
import os
import random
import re

_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sounds")
_MANIFEST = os.path.join(_DIR, "manifest.json")

# tag -> {"path": abs, "desc": str, "llm": bool}
SOUNDS: dict[str, dict] = {}


def _load():
    try:
        data = json.load(open(_MANIFEST, encoding="utf-8"))
    except Exception as e:
        print(f"[soundboard] манифест не прочитался: {e!r}", flush=True)
        return
    for tag, meta in data.get("sounds", {}).items():
        path = os.path.join(_DIR, meta.get("file", f"{tag}.mp3"))
        if os.path.isfile(path):
            SOUNDS[tag] = {"path": path, "desc": meta.get("desc", ""), "llm": bool(meta.get("llm"))}
    print(f"[soundboard] загружено звуков: {len(SOUNDS)}", flush=True)


_load()


def path(tag: str) -> str | None:
    s = SOUNDS.get(tag)
    return s["path"] if s else None


def exists(tag: str) -> bool:
    return tag in SOUNDS


def all_tags() -> list[str]:
    return sorted(SOUNDS)


def random_tag(exclude: tuple[str, ...] = ()) -> str | None:
    pool = [t for t in SOUNDS if t not in exclude] or list(SOUNDS)
    return random.choice(pool) if pool else None


def llm_menu(exclude: tuple[str, ...] = (), limit: int = 8) -> str:
    """Компактный список доступных модели звуков «tag — когда уместно», через ;.

    exclude — недавно игравшие теги, их не показываем.
    limit — сколько вариантов показать за раз. Модель жмёт первые попавшиеся из списка и
    цепляется за знакомые названия, поэтому каталог целиком ей не отдаём: каждый запрос
    получает СВОЮ случайную выборку. Без этого играли одни и те же три-четыре звука,
    сколько бы их ни лежало в папке.
    """
    items = [(t, s["desc"]) for t, s in SOUNDS.items() if s["llm"] and t not in exclude]
    if limit and len(items) > limit:
        items = random.sample(items, limit)
    return "; ".join(f"{t} — {d}" for t, d in items)


# Ключевые фразы → звук (мгновенно, без участия модели). Точные и редкие,
# чтобы не спамить на шум распознавания. Кулдаун держит вызывающая сторона.
_KEYWORD_TRIGGERS = [
    (re.compile(r"неловк\w*|повисла тишина|тишина повисла", re.I), "crickets"),
    (re.compile(r"эмоциональн\w* урон|emotional damage", re.I), "emotional-damage-meme"),
    (re.compile(r"вот это поворот|вайн ?бум|vine ?boom", re.I), "vine-boom"),
    (re.compile(r"барабанная дробь", re.I), "rimshot"),
]


def keyword_match(text: str) -> str | None:
    for rx, tag in _KEYWORD_TRIGGERS:
        if tag in SOUNDS and rx.search(text):
            return tag
    return None
