import redis.asyncio as aioredis

from app.config import settings

_pool: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
        )
    return _pool


async def close_redis():
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def publish_event(channel: str, data: str):
    r = await get_redis()
    await r.publish(channel, data)
