import os

os.environ.setdefault("WW_ADMIN_SECRET", "test-admin-secret")

import fakeredis.aioredis
import pytest

import app.redis as redis_mod
import main as main_mod
from app.ws_manager import clear_pending

ADMIN_SECRET = "test-admin-secret"
ADMIN_AUTH = {"authorization": f"Bearer {ADMIN_SECRET}"}


@pytest.fixture(autouse=True)
def _set_secrets(monkeypatch):
    """Ensure required secrets are set for all tests."""
    from app.config import settings

    monkeypatch.setattr(settings, "admin_secret", ADMIN_SECRET)


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
    clear_pending()
    yield
    main_mod._games.clear()
    main_mod._game_tasks.clear()
    clear_pending()


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
