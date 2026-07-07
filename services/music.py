import asyncio
import difflib
import re

import httpx
import yt_dlp

_YDL_OPTS = {
    "format": "bestaudio[abr<=128]/bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    # мобильные/ТВ клиенты часто не требуют логина в отличие от web
    "extractor_args": {"youtube": {"player_client": ["android", "tv"]}},
}

_http = httpx.AsyncClient(timeout=15.0, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0"})


def _best_match(entries: list[dict], query: str) -> dict:
    if len(entries) == 1:
        return entries[0]
    q = query.lower()

    def score(e):
        return difflib.SequenceMatcher(None, q, (e.get("title") or "").lower()).ratio()

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
