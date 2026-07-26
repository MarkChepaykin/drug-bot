"""Хранилище коротких голосовых нарезок по каждому человеку отдельно.

ears уже присылает речь каждого участника ОТДЕЛЬНЫМ wav-файлом (изолированно),
мозг его расшифровывает и обычно удаляет. Здесь мы оставляем копию удачных коротких
реплик, чтобы бот мог спустя время неожиданно вставить чью-то фразу его же голосом.

Хранение — в памяти + временная папка контейнера (Render всё равно эфемерный,
переживать перезапуск не требуется). Кольцевой буфер на пользователя.
"""
import os
import random
import shutil
import tempfile
import uuid
from collections import deque

CLIPS_PER_USER = 8
_ROOT = os.path.join(tempfile.gettempdir(), "drug_clips")
os.makedirs(_ROOT, exist_ok=True)

# guild_id -> user_id -> deque[{"path","text","name"}]
_store: dict[int, dict[int, deque]] = {}
_names: dict[int, str] = {}


def _bucket(guild_id: int, user_id: int) -> deque:
    return _store.setdefault(guild_id, {}).setdefault(user_id, deque(maxlen=CLIPS_PER_USER))


def add(guild_id: int, user_id: int, name: str, src_wav: str, text: str):
    """Скопировать удачную реплику в хранилище (src_wav остаётся у вызывающего)."""
    bucket = _bucket(guild_id, user_id)
    dst = os.path.join(_ROOT, f"{guild_id}_{user_id}_{uuid.uuid4().hex}.wav")
    try:
        shutil.copyfile(src_wav, dst)
    except OSError:
        return
    if len(bucket) == bucket.maxlen:  # вытесняем — чистим файл, что выпадет
        old = bucket[0]
        try:
            os.remove(old["path"])
        except OSError:
            pass
    bucket.append({"path": dst, "text": text, "name": name})
    _names[user_id] = name


def has_clips(guild_id: int) -> bool:
    return any(b for b in _store.get(guild_id, {}).values())


def random_clip(guild_id: int, exclude_user: int | None = None) -> dict | None:
    """Случайная нарезка случайного человека. По возможности не того, кто сейчас говорил."""
    users = {u: b for u, b in _store.get(guild_id, {}).items() if b}
    if not users:
        return None
    pool = {u: b for u, b in users.items() if u != exclude_user} or users
    user_id = random.choice(list(pool))
    clip = random.choice(list(pool[user_id]))
    return {"user_id": user_id, **clip}


def counts(guild_id: int) -> dict[str, int]:
    return {_names.get(u, str(u)): len(b) for u, b in _store.get(guild_id, {}).items() if b}


def clear(guild_id: int):
    for b in _store.get(guild_id, {}).values():
        for c in b:
            try:
                os.remove(c["path"])
            except OSError:
                pass
    _store.pop(guild_id, None)
