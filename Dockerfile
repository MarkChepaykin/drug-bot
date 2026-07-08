FROM node:20-bookworm-slim AS nodesrc

FROM python:3.12-slim-bookworm

COPY --from=nodesrc /usr/local/bin/node /usr/local/bin/node
COPY --from=nodesrc /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && apt-get update && apt-get install -y --no-install-recommends ffmpeg libopus0 espeak-ng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# build-essential нужен только на случай сборки нативных модулей из исходников
COPY ears/package.json ears/package-lock.json ears/
RUN apt-get update && apt-get install -y --no-install-recommends build-essential git \
    && cd ears && npm install --omit=dev --no-audit --no-fund \
    && cd /app && git clone --single-branch --branch 1.3.1 --depth 1 \
        https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git potprovider_src \
    && cd potprovider_src/server && npm ci && npx tsc \
    && mkdir -p /app/potprovider && cp -r build /app/potprovider/build \
    && cd /app && rm -rf potprovider_src \
    && apt-get purge -y build-essential git && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY . .

CMD ["bash", "start.sh"]
