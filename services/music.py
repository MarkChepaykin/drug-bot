import asyncio
import difflib
import os
import re

import httpx
import yt_dlp

# Куки настоящего YouTube-аккаунта (рекомендуется отдельный/burner, не основной —
# см. предупреждение в README) снимают часть анти-бот проверок. Кладутся как
# Render Secret File с именем youtube_cookies.txt (монтируется в /etc/secrets/),
# либо локально рядом с проектом для разработки.
_COOKIE_PATHS = ["/etc/secrets/youtube_cookies.txt", "youtube_cookies.txt"]
_cookiefile = next((p for p in _COOKIE_PATHS if os.path.isfile(p)), None)

_YDL_OPTS = {
    "format": "bestaudio[abr<=128]/bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    # Один DRM-защищённый/удалённый результат в топ-5 не должен ронять весь поиск —
    # пропускаем такой конкретный вариант и берём следующий подходящий.
    "ignoreerrors": "only_download",
    # PO-токен (bgutil, локальный сервис на 4416) — обходит часть анти-бот проверок
    # без куков; вместе с куками (если есть) даёт максимум шансов достучаться до YouTube.
    "extractor_args": {"youtubepot-bgutilhttp": {"base_url": ["http://127.0.0.1:4416"]}},
}
if _cookiefile:
    _YDL_OPTS["cookiefile"] = _cookiefile

_http = httpx.AsyncClient(timeout=15.0, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0"})


# Штрафуем каверы/минусовки/т.п., если сам запрос их не просил — иначе поиск часто
# подсовывает "Шпана (cover На Какой-то Шансон)" вместо оригинала.
_VARIANT_MARKERS = ("cover", "кавер", "минус", "instrumental", "karaoke", "speed up",
                    "nightcore", "8d audio", "reverb", "remix", "ремикс", "slowed")


def _best_match(entries: list[dict], query: str) -> dict:
    if len(entries) == 1:
        return entries[0]
    q = query.lower()
    wants_variant = any(m in q for m in _VARIANT_MARKERS)

    def score(e):
        title = (e.get("title") or "").lower()
        s = difflib.SequenceMatcher(None, q, title).ratio()
        if not wants_variant and any(m in title for m in _VARIANT_MARKERS):
            s -= 0.3
        return s

    return max(entries, key=score)


async def resolve(query: str) -> tuple[str, str]:
    """Название/ссылка → (прямой аудио-URL, название трека)."""
    q = query.strip()
    search_term = None
    if "open.spotify.com" in q:
        title = await _spotify_title(q)
        if not title:
            raise RuntimeError("не смог прочитать трек из Spotify-ссылки")
        search_term = title
    elif "music.yandex" in q:
        title = await _yandex_title(q)
        if not title:
            raise RuntimeError("не смог прочитать трек из Яндекс-ссылки")
        search_term = title
    elif not q.startswith("http"):
        search_term = q

    def _extract(target):
        with yt_dlp.YoutubeDL(_YDL_OPTS) as ydl:
            info = ydl.extract_info(target, download=False)
            if "entries" in info:
                entries = [e for e in info["entries"] if e]
                if not entries:
                    raise RuntimeError("ничего не нашёл")
                info = _best_match(entries, search_term or q)
            return info["url"], info.get("title", "трек")

    try:
        target = f"ytsearch5:{search_term}" if search_term else q
        return await asyncio.to_thread(_extract, target)
    except Exception:
        # YouTube упёрся (анти-бот) — пробуем тот же запрос на SoundCloud
        if search_term is None:
            raise
        return await asyncio.to_thread(_extract, f"scsearch5:{search_term}")


async def _spotify_title(url: str) -> str | None:
    r = await _http.get("https://open.spotify.com/oembed", params={"url": url})
    if r.status_code != 200:
        return None
    return r.json().get("title")


async def _yandex_title(url: str) -> str | None:
    r = await _http.get(url)
    m = re.search(r'<meta property="og:title" content="([^"]+)"', r.text)
    return m.group(1) if m else None
