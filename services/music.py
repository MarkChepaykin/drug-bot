import asyncio
import re

import httpx
import yt_dlp

_YDL_OPTS = {
    "format": "bestaudio[abr<=128]/bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch1",
    # мобильные/ТВ клиенты часто не требуют логина в отличие от web
    "extractor_args": {"youtube": {"player_client": ["android", "tv"]}},
}

_http = httpx.AsyncClient(timeout=15.0, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0"})


async def resolve(query: str) -> tuple[str, str]:
    """Название/ссылка → (прямой аудио-URL, название трека)."""
    q = query.strip()
    if "open.spotify.com" in q:
        title = await _spotify_title(q)
        if not title:
            raise RuntimeError("не смог прочитать трек из Spotify-ссылки")
        q = f"ytsearch1:{title}"
    elif "music.yandex" in q:
        title = await _yandex_title(q)
        if not title:
            raise RuntimeError("не смог прочитать трек из Яндекс-ссылки")
        q = f"ytsearch1:{title}"

    def _extract(target):
        with yt_dlp.YoutubeDL(_YDL_OPTS) as ydl:
            info = ydl.extract_info(target, download=False)
            if "entries" in info:
                if not info["entries"]:
                    raise RuntimeError("ничего не нашёл")
                info = info["entries"][0]
            return info["url"], info.get("title", "трек")

    try:
        return await asyncio.to_thread(_extract, q)
    except Exception:
        # YouTube упёрся — пробуем тот же запрос на SoundCloud
        if q.startswith("ytsearch1:"):
            term = q.split(":", 1)[1]
        elif not q.startswith("http"):
            term = q
        else:
            raise
        return await asyncio.to_thread(_extract, f"scsearch1:{term}")


async def _spotify_title(url: str) -> str | None:
    r = await _http.get("https://open.spotify.com/oembed", params={"url": url})
    if r.status_code != 200:
        return None
    return r.json().get("title")


async def _yandex_title(url: str) -> str | None:
    r = await _http.get(url)
    m = re.search(r'<meta property="og:title" content="([^"]+)"', r.text)
    return m.group(1) if m else None
