FROM node:20-bookworm-slim AS nodesrc

FROM python:3.12-slim-bookworm

COPY --from=nodesrc /usr/local/bin/node /usr/local/bin/node
COPY --from=nodesrc /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && apt-get update && apt-get install -y --no-install-recommends ffmpeg libopus0 espeak-ng curl \
    && rm -rf /var/lib/apt/lists/*

# PO-token сервис для yt-dlp (обход анти-бота YouTube) — готовый статический бинарник
# (Rust, без рантайм-зависимостей), никакой сборки из исходников на билд-сервере.
RUN curl -fsSL -o /usr/local/bin/bgutil-pot \
        https://github.com/jim60105/bgutil-ytdlp-pot-provider-rs/releases/download/v0.8.1/bgutil-pot-linux-x86_64 \
    && chmod +x /usr/local/bin/bgutil-pot

# Cloudflare WARP (официальный apt-репозиторий, без сборки из исходников) — в proxy mode
# отдаёт локальный SOCKS5, не требуя NET_ADMIN/TUN (недоступны на managed-хостинге Render).
# IP от Cloudflare YouTube не считает датацентровым — используется только для запроса к
# YouTube, SoundCloud-фоллбек всегда идёт напрямую и не зависит от того, поднимется ли WARP.
RUN curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | gpg --yes --dearmor \
        --output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ bookworm main" \
        > /etc/apt/sources.list.d/cloudflare-client.list \
    && apt-get update && apt-get install -y --no-install-recommends cloudflare-warp \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# build-essential нужен только на случай сборки нативных модулей из исходников
COPY ears/package.json ears/package-lock.json ears/
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && cd ears && npm install --omit=dev --no-audit --no-fund \
    && apt-get purge -y build-essential && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY . .

CMD ["bash", "start.sh"]
