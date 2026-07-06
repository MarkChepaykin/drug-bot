#!/bin/bash
# Общий секрет для внутреннего HTTP между ears (Node) и мозгом (Python).
export EARS_TOKEN="${EARS_TOKEN:-$(python -c 'import secrets; print(secrets.token_hex(16))')}"

node ears/index.js &
python bot.py &

# Если любой из процессов умер — валим весь контейнер, Render перезапустит.
wait -n
echo "[start] process exited, restarting container"
exit 1
