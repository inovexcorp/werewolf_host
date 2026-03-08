import fakeredis.aioredis
import pytest

import app.redis as redis_mod
import main as main_mod


@pytest.fixture(autouse=True)
async def fake_redis(monkeypatch):
    """Every test gets an isolated fake Redis — no real server needed."""
    server = fakeredis.aioredis.FakeServer()
    fake = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(redis_mod, "_pool", fake)
    monkeypatch.setattr(redis_mod, "get_redis", lambda: fake)
    yield fake
    await fake.aclose()


@pytest.fixture(autouse=True)
def clear_games():
    """Ensure no game state leaks between tests."""
    main_mod._games.clear()
    main_mod._game_tasks.clear()
    yield
    main_mod._games.clear()
    main_mod._game_tasks.clear()


@pytest.fixture
def async_client(fake_redis):
    """httpx AsyncClient wired to the FastAPI app, bypassing lifespan."""
    import httpx

    from main import app

    app.state.host_backstory = ""

    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def override_settings(monkeypatch):
    """Return a helper that patches app.config.settings attributes."""
    from app.config import settings

    def _override(**kwargs):
        for k, v in kwargs.items():
            monkeypatch.setattr(settings, k, v)

    return _override
