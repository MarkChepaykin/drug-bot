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
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && cd ears && npm install --omit=dev --no-audit --no-fund \
    && apt-get purge -y build-essential && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY . .

CMD ["bash", "start.sh"]
