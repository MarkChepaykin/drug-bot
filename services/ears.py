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


async def music(guild_id: int, url: str, title: str):
    r = await _client.post(f"{EARS_URL}/music", json={"guild_id": str(guild_id), "url": url, "title": title})
    r.raise_for_status()


async def skip(guild_id: int):
    r = await _client.post(f"{EARS_URL}/skip", json={"guild_id": str(guild_id)})
    r.raise_for_status()


async def stop_music(guild_id: int):
    r = await _client.post(f"{EARS_URL}/stopmusic", json={"guild_id": str(guild_id)})
    r.raise_for_status()


async def pause_music(guild_id: int):
    r = await _client.post(f"{EARS_URL}/pausemusic", json={"guild_id": str(guild_id)})
    r.raise_for_status()


async def resume_music(guild_id: int):
    r = await _client.post(f"{EARS_URL}/resumemusic", json={"guild_id": str(guild_id)})
    r.raise_for_status()


async def set_repeat(guild_id: int, on: bool):
    r = await _client.post(f"{EARS_URL}/repeat", json={"guild_id": str(guild_id), "on": on})
    r.raise_for_status()


async def queue(guild_id: int) -> dict:
    r = await _client.get(f"{EARS_URL}/queue", params={"guild_id": str(guild_id)})
    r.raise_for_status()
    return r.json()
