import asyncio
import difflib
import math
import os
import re
from typing import Awaitable, Callable

import httpx
import yt_dlp

# Куки настоящего YouTube-аккаунта (рекомендуется отдельный/burner, не основной —
# см. предупреждение в README) снимают часть анти-бот проверок. Кладутся как
# Render Secret File с именем youtube_cookies.txt (монтируется в /etc/secrets/),
# либо локально рядом с проектом для разработки.
_COOKIE_PATHS = ["/etc/secrets/youtube_cookies.txt", "youtube_cookies.txt"]
_cookiefile = next((p for p in _COOKIE_PATHS if os.path.isfile(p)), None)

_BASE_OPTS = {
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    # Один DRM-защищённый/удалённый результат в выдаче не должен ронять весь поиск —
    # пропускаем такой конкретный вариант и берём следующий подходящий.
    "ignoreerrors": "only_download",
    # PO-токен (bgutil, локальный сервис на 4416) — обходит часть анти-бот проверок
    # без куков; вместе с куками (если есть) даёт максимум шансов достучаться до YouTube.
    "extractor_args": {"youtubepot-bgutilhttp": {"base_url": ["http://127.0.0.1:4416"]}},
    "socket_timeout": 10,
}
if _cookiefile:
    _BASE_OPTS["cookiefile"] = _cookiefile

# Поиск идёт «плоским» (без вскрытия каждого результата): нужны только название,
# канал и длительность, чтобы выбрать. Так выдача приходит одним запросом и быстро,
# а потоки тянем уже у выбранного трека.
_FLAT_OPTS = {**_BASE_OPTS, "extract_flat": "in_playlist", "skip_download": True}
_STREAM_OPTS = {**_BASE_OPTS, "format": "bestaudio[abr<=128]/bestaudio/best"}

_http = httpx.AsyncClient(timeout=15.0, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0"})

# Ссылку на трек можно просто кинуть в чат — включаем без слов-команд.
LINK_RE = re.compile(
    r"https?://\S*?(?:youtube\.com|youtu\.be|open\.spotify\.com|music\.yandex\.[a-z]+|soundcloud\.com)/\S*",
    re.IGNORECASE,
)

# Сколько результатов просим у поиска и сколько запасных вариантов отдаём наверх.
SEARCH_POOL = 8
OPTIONS = 5

# Штрафуем каверы/минусовки/ускорялки, если сам запрос их не просил — иначе поиск часто
# подсовывает "Шпана (cover На Какой-то Шансон)" вместо оригинала.
_VARIANT_MARKERS = ("cover", "кавер", "минус", "instrumental", "караоке", "karaoke",
                    "speed up", "sped up", "nightcore", "8d audio", "reverb", "remix",
                    "ремикс", "slowed", "mashup", "пародия", "нейрокавер", "ai cover")
# Часовые сборники и подборки — почти никогда не то, что просили.
_MIX_MARKERS = ("сборник", "подборка", "compilation", "плейлист", "playlist", "mix ")
_LIVE_MARKERS = ("live", "лайв", "концерт", "выступление", "unplugged", "акустик", "acoustic")
# Слова, с которых начинается просьба. Режем их только В НАЧАЛЕ: в середине такое
# слово вполне может быть частью названия («Группа крови», «Музыка нас связала»).
_LEAD_WORDS = {"ну", "друг", "давай", "поставь", "включи", "запусти", "заведи", "врубай",
               "вруби", "мне", "нам", "пожалуйста", "плиз", "песню", "песня", "песни",
               "трек", "трека", "музыку", "музыка", "музыки", "эту", "этот", "какую"}
_TAIL_WORDS = {"пожалуйста", "плиз", "давай"}

# Запрос-описание, а не название: «песня из аркейна, где Экко и Джинкс», «та самая из
# рекламы», «голосом Грагаса». Буквальный поиск по таким словам находит что угодно,
# кроме нужного трека, — тут нужен тот, кто поймёт, о чём речь.
_DESCRIPTIVE_RE = re.compile(
    r"\bкотор\w+\b|\bгде\b|\bкогда\b|\b(?:та|ту|тот|то|те)\s+сам\w+|\bкак\s+в\b"
    r"|\b(?:песн\w+|трек\w*|музык\w+|саунд\w*|композици\w+|мелоди\w+)\s+(?:из|про|с)\b"
    r"|\bиз\s+(?:фильма|сериала|игры|аниме|мультик\w*|рекламы|тиктока|мема|клипа|концовки"
    r"|заставки|титров|трейлера)\b"
    r"|\bсаундтрек\w*\b|\bопенинг\w*\b|\bэндинг\w*\b|\bмем\w*\b|\bголос\w+\s+\w+"
    r"|\bиграет\s+(?:в|когда|на)\b|\bна\s+фоне\b|\bпоётся\b|\bпоется\b|\bсо\s+словами\b"
    # «что-нибудь из GTA», «любую из ведьмака» — просят не конкретный трек, а один из;
    # буквальный поиск на такое приносит обзоры и нарезки вместо музыки
    r"|\b(?:что|чего|как\w*|любую|любое|какую|какой)[-\s]?(?:то|нибудь)?\s+из\b",
    re.IGNORECASE,
)

# Ниже этого совпадения буквальный поиск считаем провалившимся: скорее всего просили
# не тем, что написано в названии, — стоит спросить у модели, о чём вообще речь.
WEAK_SCORE = 0.55


def find_link(text: str) -> str | None:
    m = LINK_RE.search(text or "")
    return m.group(0) if m else None


def _norm(s: str) -> str:
    return re.sub(r"[^\w\s]", " ", (s or "").lower()).strip()


def _tokens(s: str) -> list[str]:
    return [t for t in _norm(s).split() if len(t) > 1]


def clean_query(query: str) -> str:
    """Срезает обёртку просьбы: «друг поставь мне песню кровосток биография пожалуйста»
    → «кровосток биография». Слова из середины не трогаем — они часть названия."""
    tokens = _norm(query).split()
    while tokens and tokens[0] in _LEAD_WORDS:
        tokens.pop(0)
    while tokens and tokens[-1] in _TAIL_WORDS:
        tokens.pop()
    return " ".join(tokens) or _norm(query) or query.strip()


def looks_descriptive(query: str) -> bool:
    """Трек описан намёком, а не назван."""
    return bool(_DESCRIPTIVE_RE.search(query or ""))


def label(cand: dict) -> str:
    """Строка для списка вариантов: «Название — канал (3:45)»."""
    parts = [cand.get("title") or "трек"]
    if cand.get("uploader"):
        parts.append(cand["uploader"])
    text = " — ".join(parts)
    dur = cand.get("duration")
    if dur:
        text += f" ({int(dur) // 60}:{int(dur) % 60:02d})"
    return text


def _score(entry: dict, term: str, idx: int) -> float:
    """Насколько результат похож на то, что просили. Главная беда поиска —
    каверы, ускорялки и часовые сборники в топе, поэтому одного совпадения строк мало."""
    title = entry.get("title") or ""
    uploader = entry.get("uploader") or entry.get("channel") or ""
    title_low = _norm(title)
    hay = _norm(f"{title} {uploader}")
    q_low = _norm(term)
    q_tokens = _tokens(term)

    covered = sum(1 for t in q_tokens if t in hay) / len(q_tokens) if q_tokens else 0.0
    ratio = difflib.SequenceMatcher(None, q_low, title_low).ratio()
    s = covered * 0.7 + ratio * 0.3
    # порядок выдачи — слабая подсказка релевантности, но не решающая
    s -= idx * 0.012

    if any(m in title_low for m in _VARIANT_MARKERS) and not any(m in q_low for m in _VARIANT_MARKERS):
        s -= 0.35
    if any(m in title_low for m in _MIX_MARKERS) and not any(m in q_low for m in _MIX_MARKERS):
        s -= 0.2
    if any(m in title_low for m in _LIVE_MARKERS) and not any(m in q_low for m in _LIVE_MARKERS):
        s -= 0.15

    dur = entry.get("duration") or 0
    if dur:
        if dur < 60:
            s -= 0.4      # шортс, нарезка, отрывок
        elif dur > 900:
            s -= 0.35     # часовой сборник, а не трек
        elif dur > 600:
            s -= 0.15
        elif 90 <= dur <= 420:
            s += 0.1      # нормальная длина песни

    up_low = uploader.lower()
    if up_low.endswith("- topic") or "official" in title_low or "vevo" in up_low:
        s += 0.12         # авто-каналы лейблов и официальные заливки = оригинал

    views = entry.get("view_count") or 0
    if views > 0:
        s += min(0.1, math.log10(views) / 80)
    return s


def _candidate(entry: dict, score: float = 0.0) -> dict:
    url = entry.get("url") or entry.get("webpage_url")
    if not url and entry.get("id"):
        url = f"https://www.youtube.com/watch?v={entry['id']}"
    return {
        "url": url,
        "title": entry.get("title") or "трек",
        "uploader": entry.get("uploader") or entry.get("channel") or "",
        "duration": entry.get("duration"),
        "score": round(score, 3),
    }


def _entries(info) -> list[dict]:
    """yt-dlp при полном провале извлечения возвращает None, а не бросает — без этой
    проверки дальше летело «argument of type 'NoneType' is not iterable»."""
    if not info:
        return []
    if isinstance(info, dict) and info.get("entries") is not None:
        return [e for e in info["entries"] if e]
    return [info] if isinstance(info, dict) else []


async def _search_raw(term: str, prefix: str, count: int) -> list[dict]:
    def _run():
        with yt_dlp.YoutubeDL(_FLAT_OPTS) as ydl:
            return _entries(ydl.extract_info(f"{prefix}{count}:{term}", download=False))
    try:
        return await asyncio.to_thread(_run)
    except Exception as e:
        print(f"[music] поиск {prefix} упал: {e!r}", flush=True)
        return []


def _rank(entries: list[dict], term: str, limit: int = OPTIONS) -> list[dict]:
    scored = [(_score(e, term, i), e) for i, e in enumerate(entries)]
    scored.sort(key=lambda p: -p[0])
    cands = [_candidate(e, sc) for sc, e in scored]
    return [c for c in cands if c["url"]][:limit]


async def _find(term: str, prefix: str = "ytsearch") -> list[dict]:
    return _rank(await _search_raw(term, prefix, SEARCH_POOL), term)


def _best(cands: list[dict]) -> float:
    return cands[0]["score"] if cands else 0.0


async def search(query: str, limit: int = OPTIONS) -> list[dict]:
    """Название → список вариантов, лучший первым."""
    term = clean_query(query)
    entries = await _search_raw(term, "ytsearch", SEARCH_POOL)
    if not entries:
        # YouTube упёрся (анти-бот) — тот же запрос на SoundCloud
        entries = await _search_raw(term, "scsearch", SEARCH_POOL)
    if not entries:
        raise RuntimeError(f"ничего не нашёл по запросу «{term}»")
    return _rank(entries, term, limit)


async def _first_playable(cands: list[dict], tries: int = 3):
    """Первый вариант, который реально отдаёт звук → (url, название, индекс).
    Верхний результат бывает удалён, заблокирован по региону или закрыт анти-ботом."""
    for i, cand in enumerate(cands[:tries]):
        try:
            url, title = await stream(cand["url"])
        except Exception as e:
            print(f"[music] вариант «{cand['title']}» не открылся: {e!r}", flush=True)
            continue
        return url, title or cand["title"], i
    return None


async def stream(url: str) -> tuple[str, str]:
    """Ссылка на конкретный трек → (прямой аудио-URL, название)."""
    def _run():
        with yt_dlp.YoutubeDL(_STREAM_OPTS) as ydl:
            entries = _entries(ydl.extract_info(url, download=False))
        if not entries:
            raise RuntimeError("трек не открылся")
        info = entries[0]
        direct = info.get("url") or next(
            (f.get("url") for f in info.get("requested_formats") or [] if f.get("url")), None
        )
        if not direct:
            raise RuntimeError("нет аудио-потока")
        return direct, info.get("title") or "трек"
    return await asyncio.to_thread(_run)


# Две подсказки от модели, которые music принимает снаружи, чтобы не зависеть от llm:
#   identify — «просьба → поисковый запрос» (что это вообще за трек);
#   pick     — «просьба + список найденного → номер нужного» (узнать в выдаче).
Identifier = Callable[[str], Awaitable[str | None]]
Picker = Callable[[str, list[str]], Awaitable[int | None]]

# Сколько вариантов показываем модели на выбор.
PICK_POOL = 10


async def _safe(coro, what: str):
    try:
        return await coro
    except Exception as e:  # модель молчит/лимит — работаем как без неё
        print(f"[music] {what} не вышло: {e!r}", flush=True)
        return None


def _merge(*lists: list[dict]) -> list[dict]:
    """Вперемешку, по одному из каждой выдачи: модель должна увидеть и то, что нашлось
    буквально, и то, что нашлось по её догадке, а не десять штук из одной."""
    out, seen = [], set()
    for row in zip(*(lst + [None] * (max(map(len, lists)) - len(lst)) for lst in lists)):
        for c in row:
            if c and c["url"] not in seen:
                seen.add(c["url"])
                out.append(c)
    return out


async def _smart(query: str, term: str, cands: list[dict], guess: str | None,
                 pick: Picker | None) -> tuple[str, list[dict]]:
    """Описание вместо названия («песня из Аркейна, где Экко и Джинкс») буквальный поиск
    вытягивает редко. Добавляем к выдаче поиск по догадке модели и даём ей же выбрать
    нужное из того, что реально нашлось — узнать трек в списке ей куда проще, чем
    вспомнить его название по описанию."""
    g_term, g_cands = "", []
    if guess:
        g_term = clean_query(guess)
        if g_term and g_term != term:
            g_cands = await _find(g_term)
        else:
            g_term = ""
    pool = _merge(cands, g_cands)
    if pick and pool:
        n = await _safe(pick(query, [label(c) for c in pool[:PICK_POOL]]), "выбор трека")
        if n:
            chosen = pool[n - 1]
            print(f"[music] «{query}» → выбрал «{chosen['title']}»", flush=True)
            return (g_term or term), [chosen] + [c for c in pool if c is not chosen]
    # Модель не выбрала из найденного — берём её догадку, только если по ней нашлось
    # что-то действительно похожее: догадки бывают выдуманные, и поиск по такой выдумке
    # даёт мусор, который лучше не ставить вместо буквального результата.
    if _best(g_cands) > max(_best(cands), WEAK_SCORE):
        print(f"[music] «{query}» понял как «{g_term}»", flush=True)
        return g_term, g_cands
    return term, cands


async def resolve_options(query: str, identify: Identifier | None = None,
                          pick: Picker | None = None) -> tuple[str, str, list[dict]]:
    """Название/описание/ссылка → (прямой аудио-URL, название, запасные варианты).
    Запасные нужны, чтобы человек сказал «не то» и переключился, не диктуя запрос заново."""
    q = query.strip()
    term = None
    if "open.spotify.com" in q:
        term = await _spotify_title(q)
        if not term:
            raise RuntimeError("не смог прочитать трек из Spotify-ссылки")
    elif "music.yandex" in q:
        term = await _yandex_title(q)
        if not term:
            raise RuntimeError("не смог прочитать трек из Яндекс-ссылки")
    elif not q.startswith("http"):
        term = q

    if term is None:  # прямая ссылка — играем ровно её, без вариантов
        url, title = await stream(q)
        return url, title, []

    term = clean_query(term)
    if identify and looks_descriptive(q):
        # описание: буквальный поиск скорее всего мимо, поэтому не ждём его результата,
        # а спрашиваем модель параллельно — лишнего времени это не стоит
        cands, guess = await asyncio.gather(_find(term), _safe(identify(q), "опознать трек"))
        term, cands = await _smart(q, term, cands, guess, pick)
    else:
        cands = await _find(term)
        if (identify or pick) and _best(cands) < WEAK_SCORE:
            # непохожая выдача — просьбу либо расслышали криво, либо описали трек словами,
            # которых нет в названии; обе беды лечит модель
            guess = await _safe(identify(q), "опознать трек") if identify else None
            term, cands = await _smart(q, term, cands, guess, pick)

    picked = await _first_playable(cands)
    if picked is None:
        # YouTube нашёл, но не отдаёт звук (анти-бот, протухшие куки, бан по региону) —
        # ищем тот же трек на SoundCloud, иначе бот просто молчит вместо музыки.
        # Ищем по НАЗВАНИЮ найденного: если просили описанием («песня из Аркейна, где
        # Экко и Джинкс»), сама просьба на SoundCloud не найдёт ничего.
        found = cands[0]["title"] if cands else ""
        for sc_term in dict.fromkeys(t for t in (clean_query(found), term) if t):
            sc = await _find(sc_term, "scsearch")
            picked = await _first_playable(sc)
            if picked:
                cands = sc
                break
    if picked is None:
        raise RuntimeError(f"ничего не нашёл по запросу «{term}»" if not cands
                           else f"нашёл «{term}», но ни один вариант не открылся")
    url, title, idx = picked
    return url, title, [c for j, c in enumerate(cands) if j != idx]


async def resolve(query: str, identify: Identifier | None = None,
                  pick: Picker | None = None) -> tuple[str, str]:
    url, title, _ = await resolve_options(query, identify, pick)
    return url, title


async def _spotify_title(url: str) -> str | None:
    r = await _http.get("https://open.spotify.com/oembed", params={"url": url})
    if r.status_code != 200:
        return None
    return r.json().get("title")


async def _yandex_title(url: str) -> str | None:
    r = await _http.get(url)
    m = re.search(r'<meta property="og:title" content="([^"]+)"', r.text)
    return m.group(1) if m else None
