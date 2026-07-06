import os

import httpx

EARS_URL = "http://127.0.0.1:" + os.getenv("EARS_PORT", "8300")
_client = httpx.AsyncClient(timeout=30.0)


async def join(guild_id: int, channel_id: int):
    r = await _client.post(f"{EARS_URL}/join", json={"guild_id": str(guild_id), "channel_id": str(channel_id)})
    r.raise_for_status()


async def leave(guild_id: int):
    r = await _client.post(f"{EARS_URL}/leave", json={"guild_id": str(guild_id)})
    r.raise_for_status()


async def play(guild_id: int, path: str):
    r = await _client.post(f"{EARS_URL}/play", json={"guild_id": str(guild_id), "path": path})
    r.raise_for_status()
