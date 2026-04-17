import logging

from starlette.requests import Request
from starlette.responses import StreamingResponse

from app.config import settings
from app.redis import get_redis

logger = logging.getLogger(__name__)


async def _event_generator(channel: str, log_key: str, request: Request):
    r = await get_redis()
    pubsub = r.pubsub()

    # Subscribe FIRST so we don't miss events during replay
    await pubsub.subscribe(channel)

    # Replay historical events, capped to the most recent N
    events = await r.lrange(log_key, -settings.spectator_replay_cap, -1)
    for event_data in events:
        if await request.is_disconnected():
            break
        yield f"data: {event_data}\n\n"

    try:
        while True:
            if await request.is_disconnected():
                break
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=settings.spectator_heartbeat_seconds,
            )
            if message and message["type"] == "message":
                yield f"data: {message['data']}\n\n"
            else:
                yield ": keepalive\n\n"
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()


def _stream(channel: str, log_key: str, request: Request) -> StreamingResponse:
    return StreamingResponse(
        _event_generator(channel, log_key, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def spectator_stream(game_id: str, request: Request) -> StreamingResponse:
    return _stream(
        f"game:{game_id}:events",
        f"game:{game_id}:event_log",
        request,
    )


def series_spectator_stream(series_id: str, request: Request) -> StreamingResponse:
    return _stream(
        f"series:{series_id}:events",
        f"series:{series_id}:event_log",
        request,
    )
