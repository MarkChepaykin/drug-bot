# Discord-бот: шутник + болталка

Заходит в голосовой канал, слушает разговор, периодически вставляет голосом
глупые шутки по теме. Отвечает текстом, если его упомянуть.

**Стек:** py-cord · Groq Whisper (распознавание речи) · Groq LLM (шутки/диалог) · edge-tts (озвучка). Всё бесплатно.

Цепочка: `слушает войс → Whisper (текст) → LLM (шутка) → edge-tts (mp3) → играет в войс`.

---

## Что нужно один раз

1. **Python 3.10+**
2. **ffmpeg** в PATH — нужен для проигрывания/обработки звука.
   - Windows: `winget install Gyan.FFmpeg` (затем перезапустить терминал)
   - Linux (Orange Pi): `sudo apt install ffmpeg`
3. **Токен бота:** https://discord.com/developers/applications → New Application → Bot → Reset Token.
   - На вкладке **Bot** включи **Message Content Intent** и **Server Members Intent**.
   - Пригласи бота на сервер: вкладка **OAuth2 → URL Generator**, скоупы `bot` + `applications.commands`, права: `Connect`, `Speak`, `Send Messages`, `Use Voice Activity`.
4. **Ключ Groq (бесплатный):** https://console.groq.com/keys

## Установка

```bash
cd C:\Users\user\Projects\discord-bot
python -m venv .venv
.venv\Scripts\activate          # Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # Linux: cp .env.example .env
```

Открой `.env`, впиши `DISCORD_TOKEN`, `GROQ_API_KEY` и (для быстрых тестов) `DEBUG_GUILD` —
ID своего сервера, чтобы слэш-команды появились мгновенно.

## Запуск

```bash
python bot.py
```

## Деплой на Render (рекомендуется)

Хостинг за границей — Groq и Discord работают напрямую, никакой обход блокировок не нужен.

1. Render.com → New → **Blueprint** → Public Git Repository → URL этого репозитория.
2. Ввести env vars: `DISCORD_TOKEN`, `GROQ_API_KEY`, `DEBUG_GUILD` (ID сервера).
   Токен вводить целиком — поле в Render UI может обрезать по спецсимволу.
3. Готово: собирается Docker-образ (ffmpeg и libopus внутри), free-инстанс не засыпает
   благодаря самопингу `RENDER_EXTERNAL_URL` каждые 10 минут.

Free-лимит Render — 750 инстанс-часов в месяц на аккаунт: один бот 24/7 = весь лимит,
второго бота на том же аккаунте держать не выйдет.

## Команды

| Команда   | Что делает                                            |
|-----------|-------------------------------------------------------|
| `/join`   | заходит в твой голосовой канал, слушает и шутит        |
| `/leave`  | выходит из канала                                      |
| `/joke`   | пошутить прямо сейчас                                   |
| `@бот ...` | (в текстовом чате) ответит на сообщение                |

## Настройки (`.env`)

- `WINDOW_SECONDS` — окно записи перед распознаванием (по умолчанию 25 с).
- `JOKE_INTERVAL` — минимум секунд между шутками (180).
- `JOKE_CHANCE` — вероятность пошутить, когда интервал прошёл (0.7).
- `TTS_VOICE` — голос озвучки. Русские: `ru-RU-DmitryNeural`, `ru-RU-SvetlanaNeural`.
- `LLM_MODEL`, `STT_MODEL` — модели Groq.

---

## ⚠️ Блокировки в РФ (только для локального запуска)

Discord (и YouTube для будущего музыкального модуля) заблокированы. Голос Discord идёт
по **UDP**, обычный прокси его не пропустит. Решение — обход DPI на уровне пакетов,
он работает и с UDP:

- **Windows-хост:** [flowseal/zapret-discord-youtube](https://github.com/flowseal/zapret-discord-youtube) —
  запустить нужный `.bat`, оставить работать фоном, потом запускать бота.
- **Orange Pi / Linux-хост:** upstream [bol-van/zapret](https://github.com/bol-van/zapret) —
  ставится как сервис, есть стратегии под Discord/YouTube.

`zapret` запускается **отдельно** от бота — в коде ничего настраивать не нужно.
API Groq и edge-tts из РФ доступны напрямую, обход им не требуется.

---

## Дальше по плану

- 🎵 Музыкальный модуль (YouTube/Spotify/Яндекс через поиск) — Lavalink + yt-dlp.
- 🗣️ Голосовой диалог (отвечать голосом, а не только текстом).
- 📝 «Что я пропустил» — пересказ разговора.
