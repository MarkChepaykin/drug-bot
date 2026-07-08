#!/bin/bash
# Общий секрет для внутреннего HTTP между ears (Node) и мозгом (Python).
export EARS_TOKEN="${EARS_TOKEN:-$(python -c 'import secrets; print(secrets.token_hex(16))')}"

# PO-token сервер для yt-dlp (обход антибота YouTube) — второстепенный, если упадёт,
# просто перезапускаем его в цикле, не роняя весь бот и голосовую сессию.
(while true; do node /app/potprovider/build/main.js; sleep 5; done) &

node ears/index.js &
python bot.py &

# Если ears или bot умерли — валим контейнер, Render перезапустит.
wait -n %2 %3
echo "[start] process exited, restarting container"
exit 1
