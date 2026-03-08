import asyncio
import json
import logging

import redis.asyncio as aioredis
from starlette.requests import Request
from starlette.responses import StreamingResponse

from app.redis import get_redis

logger = logging.getLogger(__name__)


async def _event_generator(game_id: str, request: Request):
    r = await get_redis()
    pubsub = r.pubsub()
    channel = f"game:{game_id}:events"
    await pubsub.subscribe(channel)

    try:
        while True:
            if await request.is_disconnected():
                break
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if message and message["type"] == "message":
                data = message["data"]
                yield f"data: {data}\n\n"
            else:
                # Send keepalive comment every second
                yield ": keepalive\n\n"
                await asyncio.sleep(1)
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()


def spectator_stream(game_id: str, request: Request) -> StreamingResponse:
    return StreamingResponse(
        _event_generator(game_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
