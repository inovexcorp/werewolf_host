import asyncio
import json
import logging
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi import WebSocket as FastAPIWebSocket
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.avatar import default_avatar_path, ensure_avatar_dir, process_avatar
from app.config import settings
from app.engine import GameEngine
from app.models.game import Player
from app.narrator import Narrator
from app.rate_limiter import rate_limiter
from app.redis import close_redis, get_redis, publish_event
from app.spectator import series_spectator_stream, spectator_stream
from app.ws_manager import (
    agent_connected,
    agent_disconnected,
    create_close_event,
    get_connected_agents,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

game_not_found: str = "Game not found"
team_not_found: str = "Team not found"


class _HealthCheckFilter(logging.Filter):
    """Suppresses noisy health-check GET requests from the uvicorn access log."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "GET /health" in message:
            logger.debug("Health check pinged (suppressed from access log)")
            return False
        return True


logging.getLogger("uvicorn.access").addFilter(_HealthCheckFilter())

# Track running games
_games: dict[str, GameEngine] = {}
_game_tasks: dict[str, asyncio.Task] = {}

# Track running series
_series_tasks: dict[str, asyncio.Task] = {}


def _redact_key(key: str) -> str:
    """Return a redacted version of a secret key for safe logging."""
    if not key:
        return "(not set)"
    return f"{key[:4]}...{key[-4:]}" if len(key) > 8 else "****"


def _log_settings_summary():
    """Log all relevant configuration values at startup (secrets are redacted)."""
    logger.info("=== Werewolf Host Configuration ===")
    logger.info("  Redis URL:       %s", settings.redis_url)
    logger.info("  Narrator model:  %s", settings.narrator_model)
    logger.info("  OpenAI base URL: %s", settings.openai_base_url)
    logger.info("  OpenAI API key:  %s", _redact_key(settings.openai_api_key))
    logger.info("  Admin secret:    %s", _redact_key(settings.admin_secret))
    logger.info("  Disable docs:    %s", settings.disable_docs)
    logger.info("  Intro duration:  %ds", settings.introduction_duration)
    logger.info("  Night duration:  %ds", settings.night_duration)
    logger.info("  Discussion dur:  %ds", settings.discussion_duration)
    logger.info("  Voting duration: %ds", settings.voting_duration)
    if not settings.openai_api_key:
        logger.warning("No OpenAI API key set — narrator features will be disabled")
    logger.info("===================================")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan handler: initializes Redis, avatars, and narrator on startup;
    cancels running games/series and closes Redis on shutdown."""
    # Log configuration summary
    _log_settings_summary()

    # Ensure avatar directory & default avatar exist
    ensure_avatar_dir()

    # Verify Redis connection
    try:
        r = await get_redis()
        await r.ping()
        logger.info("Connected to Redis")
    except Exception as exc:
        logger.critical("Failed to connect to Redis at %s: %s", settings.redis_url, exc)
        raise RuntimeError(
            f"Cannot connect to Redis at {settings.redis_url}. "
            "Is Redis running? Check WW_REDIS_URL."
        ) from exc

    # Generate host backstory via LLM
    narrator = Narrator()
    backstory = await narrator.generate_host_backstory()
    app.state.host_backstory = backstory
    if backstory:
        logger.info("Host backstory: %s", backstory)
    else:
        logger.warning("No host backstory generated (narrator may be disabled)")

    yield
    # Shutdown
    for task in _series_tasks.values():
        task.cancel()
    for task in _game_tasks.values():
        task.cancel()
    await close_redis()


app = FastAPI(
    title="Werewolf Host",
    version="0.1.0",
    lifespan=lifespan,
    **(
        {"docs_url": None, "redoc_url": None, "openapi_url": None}
        if settings.disable_docs
        else {}
    ),
)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
app.mount("/static", StaticFiles(directory=_PROJECT_ROOT / "static"), name="static")

if not settings.disable_docs:

    @app.get("/")
    async def root():
        return RedirectResponse(url="/docs")


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def _require_admin_secret(request: Request) -> None:
    """FastAPI dependency that validates the Bearer token matches the admin secret."""
    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {settings.admin_secret}":
        raise HTTPException(403, "Invalid or missing admin secret")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    """Body for POST /register — registers or re-registers a team."""

    team_name: str = Field(
        pattern=r"^[A-Za-z0-9 _\-]{3,32}$",
        description=(
            "3-32 chars: letters, digits, space, underscore, hyphen. "
            "Restricted to prevent prompt-injection via LLM narration."
        ),
    )
    avatar: str | None = None  # optional base64-encoded image


class CreateGameRequest(BaseModel):
    """Body for POST /games — creates a new game with selected (or all) teams."""

    team_names: list[str] | None = None  # None = use all registered teams
    player_count: int | None = None


class CreateSeriesRequest(BaseModel):
    """Body for POST /games/series — kicks off a multi-game series."""

    name: str
    num_games: int  # must be >= 2
    team_names: list[str] | None = None


class GameStatusResponse(BaseModel):
    """Response model for GET /games/{game_id} with current game state."""

    game_id: str
    phase: str
    round: int
    alive_players: list[str]
    winner: str | None = None


# ---------------------------------------------------------------------------
# Team registration
# ---------------------------------------------------------------------------


@app.post(
    "/register",
    responses={
        400: {"description": "Invalid avatar"},
        403: {"description": "Invalid token for re-registration"},
        409: {"description": "Team is in an active game"},
    },
)
async def register_team(req: RegisterRequest, request: Request):
    """Register a new team or re-register an existing one.

    On re-registration the caller must supply the original token via Bearer auth.
    Generates a new auth token, stores the team in Redis, and processes the avatar.
    """
    r = await get_redis()

    old_token = await r.hget("teams", req.team_name)
    if old_token:
        # Re-registration: require existing token
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {old_token}":
            raise HTTPException(403, "Re-registration requires valid existing token")

        # Block if team is in an active game
        for game_id, engine in _games.items():
            if game_id in _game_tasks and req.team_name in engine.state.players:
                raise HTTPException(409, "Cannot re-register while in an active game")

        await r.hdel("team_tokens", old_token)

    token = secrets.token_urlsafe(32)
    await r.hset("teams", req.team_name, token)
    await r.hset("team_tokens", token, req.team_name)

    agent_id = req.team_name

    if req.avatar:
        try:
            avatar_path = process_avatar(req.avatar, req.team_name)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
    else:
        avatar_path = default_avatar_path()

    await r.hset("team_avatars", req.team_name, avatar_path)

    return {
        "agent_id": agent_id,
        "team_name": req.team_name,
        "token": token,
        "avatar_url": f"/{avatar_path}",
        "status": "registered",
    }


@app.get("/teams")
async def list_teams():
    """Return all registered teams with their avatar URLs and connection status."""
    r = await get_redis()
    teams = await r.hgetall("teams")
    avatars = await r.hgetall("team_avatars")
    default = default_avatar_path()
    connected = get_connected_agents()
    return {
        "teams": [
            {
                "team_name": k,
                "avatar_url": f"/{avatars.get(k, default)}",
                "connected": k in connected,
            }
            for k in teams
        ]
    }


@app.delete(
    "/teams/{team_name}",
    responses={
        403: {"description": "Invalid or missing admin secret"},
        404: {"description": team_not_found},
    },
)
async def unregister_team(
    team_name: str,
    _: Annotated[None, Depends(_require_admin_secret)],
):
    """Remove a team from the registry. Requires admin auth."""
    r = await get_redis()
    old_token = await r.hget("teams", team_name)
    removed = await r.hdel("teams", team_name)
    if not removed:
        raise HTTPException(404, team_not_found)
    if old_token:
        await r.hdel("team_tokens", old_token)
    return {"status": "removed"}


# ---------------------------------------------------------------------------
# Health & status checks
# ---------------------------------------------------------------------------


@app.get("/health")
async def health_check():
    """Liveness probe: Redis connectivity, team count, active games."""
    redis_ok = True
    try:
        r = await get_redis()
        await r.ping()
    except Exception:
        redis_ok = False

    team_count = await r.hlen("teams") if redis_ok else 0
    active_games = len(_game_tasks)

    return {
        "status": "ok" if redis_ok else "degraded",
        "redis": "ok" if redis_ok else "unavailable",
        "registered_teams": team_count,
        "active_games": active_games,
    }


@app.get(
    "/teams/{team_name}/status",
    responses={404: {"description": team_not_found}},
)
async def team_status(team_name: str):
    """Return registration and connection status for a single team."""
    r = await get_redis()
    token = await r.hget("teams", team_name)
    if token is None:
        raise HTTPException(404, team_not_found)

    avatars = await r.hgetall("team_avatars")
    avatar_path = avatars.get(team_name, default_avatar_path())

    connected = get_connected_agents()
    return {
        "registered": True,
        "team_name": team_name,
        "avatar_url": f"/{avatar_path}",
        "connected": team_name in connected,
    }


# ---------------------------------------------------------------------------
# Team stats (persistent, backed by metrics indexer)
# ---------------------------------------------------------------------------


@app.get(
    "/teams/{team_name}/stats",
    responses={404: {"description": team_not_found}},
)
async def team_stats(team_name: str):
    """Return aggregate stats for a team: rank, win/loss, role history."""
    r = await get_redis()
    token = await r.hget("teams", team_name)
    if token is None:
        raise HTTPException(404, team_not_found)

    # Scoreboard data
    score = await r.zscore("scoreboard", team_name)
    rank = await r.zrevrank("scoreboard", team_name)

    # Persistent stats from metrics indexer
    raw = await r.hgetall(f"team_stats:{team_name}")

    def g(key: str) -> int:
        return int(raw.get(key, 0))

    return {
        "team_name": team_name,
        "score": int(score) if score is not None else 0,
        "rank": rank,
        "games_played": g("games_played"),
        "games_won": g("games_won"),
        "games_lost": g("games_lost"),
        "roles": {
            "werewolf": g("role_werewolf"),
            "villager": g("role_villager"),
            "seer": g("role_seer"),
            "guard": g("role_guard"),
        },
        "wins_by_role": {
            "werewolf": g("wins_as_werewolf"),
            "villager": g("wins_as_villager"),
            "seer": g("wins_as_seer"),
            "guard": g("wins_as_guard"),
        },
        "times_murdered": g("times_murdered"),
        "times_banished": g("times_banished"),
        "times_survived": g("times_survived"),
    }


# ---------------------------------------------------------------------------
# Agent WebSocket endpoint
# ---------------------------------------------------------------------------


@app.websocket("/ws/agent")
async def agent_ws_endpoint(websocket: FastAPIWebSocket):
    """Accept an inbound WebSocket from an AI agent.

    Authenticates via `Authorization: Bearer <token>` header, registers the
    connection, and keeps the socket alive until the agent disconnects.
    """
    auth = websocket.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        await websocket.close(
            code=4001, reason="Missing or malformed Authorization header"
        )
        return
    token = auth.removeprefix("Bearer ")

    r = await get_redis()
    team_name = await r.hget("team_tokens", token)
    if not team_name:
        await websocket.close(code=4001, reason="Invalid token")
        return

    await websocket.accept()
    if not agent_connected(team_name, websocket):
        logger.info(
            "Rejecting duplicate WebSocket for team %s from %s",
            team_name,
            websocket.client,
        )
        await websocket.close(code=4002, reason="Team already has an active connection")
        return

    logger.info("Agent %s connected via WebSocket", team_name)
    close_event = create_close_event(team_name)
    try:
        # Don't read from the socket here — _listen_loop in ConnectionManager
        # is the sole reader.  Just keep the endpoint alive until signalled.
        await close_event.wait()
    except Exception:
        logger.exception("Agent %s WebSocket error", team_name)
    finally:
        agent_disconnected(team_name)


# ---------------------------------------------------------------------------
# Game lifecycle
# ---------------------------------------------------------------------------


async def _create_game_internal(
    app_state, team_names: list[str] | None = None, series_id: str | None = None
) -> tuple[str, GameEngine]:
    """Create a game with registered teams. Returns (game_id, engine)."""
    r = await get_redis()
    all_teams = await r.hgetall("teams")

    if not all_teams:
        raise HTTPException(400, "No teams registered")

    if team_names:
        missing = [t for t in team_names if t not in all_teams]
        if missing:
            raise HTTPException(400, f"Teams not found: {missing}")
        selected = {t: all_teams[t] for t in team_names}
    else:
        selected = dict(all_teams)

    if len(selected) < 5:
        raise HTTPException(400, f"Need at least 5 teams, got {len(selected)}")

    avatars = await r.hgetall("team_avatars")
    default = default_avatar_path()

    game_id = f"game_{uuid.uuid4().hex[:8]}"
    players = []
    for team_name in selected:
        avatar_path = avatars.get(team_name, default)
        players.append(
            Player(
                id=team_name,
                team=team_name,
                avatar_url=f"/{avatar_path}",
            )
        )

    backstory = getattr(app_state, "host_backstory", "")
    narrator = Narrator(host_backstory=backstory)
    engine = GameEngine(
        game_id, players, narrator, host_backstory=backstory, series_id=series_id
    )
    _games[game_id] = engine

    # Store game metadata in Redis
    await r.set(
        f"game:{game_id}:meta",
        json.dumps(
            {
                "game_id": game_id,
                "teams": list(selected.keys()),
                "status": "created",
            }
        ),
    )

    return game_id, engine


async def _start_game_internal(game_id: str, engine: GameEngine) -> asyncio.Task:
    """Connect agents via WebSocket and launch the game loop as a task."""
    failures = await engine.setup()
    if failures:
        raise HTTPException(
            502,
            f"Failed to connect to agents: {failures}",
        )

    async def _run_game():
        try:
            await engine.run()
        except Exception:
            logger.exception("Game %s crashed", game_id)
        finally:
            _game_tasks.pop(game_id, None)
            rate_limiter.clear_for_game(game_id)

    task = asyncio.create_task(_run_game())
    _game_tasks[game_id] = task
    return task


@app.post(
    "/games",
    responses={
        400: {"description": "No teams / missing teams / too few teams"},
        403: {"description": "Invalid or missing admin secret"},
    },
)
async def create_game(
    req: CreateGameRequest,
    request: Request,
    _: Annotated[None, Depends(_require_admin_secret)],
):
    """Create a new game (but don't start it yet). Requires admin auth."""
    game_id, engine = await _create_game_internal(
        request.app.state, team_names=req.team_names
    )
    return {
        "game_id": game_id,
        "players": len(engine.state.players),
        "status": "created",
    }


@app.post(
    "/games/{game_id}/start",
    responses={
        400: {"description": "Game already running"},
        403: {"description": "Invalid or missing admin secret"},
        404: {"description": game_not_found},
        502: {"description": "Failed to connect to agents"},
    },
)
async def start_game(
    game_id: str,
    _: Annotated[None, Depends(_require_admin_secret)],
):
    """Start a previously created game: connect agents and kick off the game loop."""
    engine = _games.get(game_id)
    if not engine:
        raise HTTPException(404, game_not_found)
    if game_id in _game_tasks:
        raise HTTPException(400, "Game already running")

    await _start_game_internal(game_id, engine)

    return {"game_id": game_id, "status": "started"}


@app.get("/games")
async def list_games():
    """List all games (active first), including phase, player counts, and winner."""
    games = []
    for game_id, engine in _games.items():
        s = engine.state
        games.append(
            {
                "game_id": s.game_id,
                "phase": s.phase,
                "round": s.round,
                "players": len(s.players),
                "alive_players": len(s.alive_player_ids),
                "winner": s.winner,
                "active": game_id in _game_tasks,
            }
        )
    # Active games first, then by game_id
    games.sort(key=lambda g: (not g["active"], g["game_id"]))
    return {"games": games}


@app.get("/games/{game_id}", responses={404: {"description": game_not_found}})
async def get_game_status(game_id: str) -> GameStatusResponse:
    """Return current game status: phase, round, alive players, winner."""
    engine = _games.get(game_id)
    if not engine:
        raise HTTPException(404, game_not_found)
    return GameStatusResponse(
        game_id=engine.state.game_id,
        phase=engine.state.phase,
        round=engine.state.round,
        alive_players=engine.state.alive_player_ids,
        winner=engine.state.winner,
    )


@app.get(
    "/games/{game_id}/players",
    responses={
        403: {"description": "Invalid or missing admin secret"},
        404: {"description": game_not_found},
    },
)
async def get_game_players(
    game_id: str,
    _: Annotated[None, Depends(_require_admin_secret)],
):
    """Return detailed player info (roles, alive status) for a game. Admin only."""
    engine = _games.get(game_id)
    if not engine:
        raise HTTPException(404, game_not_found)
    return {
        "game_id": game_id,
        "players": [
            {
                "id": p.id,
                "team": p.team,
                "role": p.role,
                "avatar_url": p.avatar_url,
                "alive": p.alive,
            }
            for p in engine.state.players.values()
        ],
    }


@app.get(
    "/games/{game_id}/spectate",
    responses={
        403: {"description": "Invalid or missing admin secret"},
        404: {"description": game_not_found},
    },
)
async def spectate_game(
    game_id: str,
    request: Request,
    _: Annotated[None, Depends(_require_admin_secret)],
):
    """Open an SSE stream of real-time game events for spectators. Admin only."""
    if game_id not in _games:
        raise HTTPException(404, game_not_found)
    return spectator_stream(game_id, request)


# ---------------------------------------------------------------------------
# Series orchestration
# ---------------------------------------------------------------------------


async def _publish_series_update(series_id: str, r):
    """Publish the current series state to the Redis pub/sub channel for spectators."""
    meta = await r.hgetall(f"series:{series_id}:meta")
    payload = json.dumps(
        {
            "event": "series_update",
            "series_id": series_id,
            "name": meta.get("name", ""),
            "total_games": int(meta.get("total_games", 0)),
            "completed_games": int(meta.get("completed_games", 0)),
            "current_game_id": meta.get("current_game_id", ""),
            "game_ids": json.loads(meta.get("game_ids", "[]")),
            "status": meta.get("status", ""),
            "delay_seconds": settings.multi_game_delay,
        }
    )
    await publish_event(f"series:{series_id}:events", payload)


async def _run_series(
    series_id: str,
    name: str,
    total_games: int,
    team_names: list[str] | None,
    app_state,
):
    """Run a multi-game series sequentially, with a configurable delay between games.

    Creates, starts, and awaits each game in order, updating Redis metadata and
    publishing progress events for spectators after each game completes.
    """
    r = await get_redis()
    try:
        for i in range(total_games):
            game_id, engine = await _create_game_internal(
                app_state, team_names, series_id=series_id
            )

            # Update Redis meta
            raw_ids = await r.hget(f"series:{series_id}:meta", "game_ids")
            game_ids = json.loads(raw_ids) if raw_ids else []
            game_ids.append(game_id)
            await r.hset(
                f"series:{series_id}:meta",
                mapping={
                    "current_game_id": game_id,
                    "game_ids": json.dumps(game_ids),
                    "status": "running",
                },
            )

            # Store mappings
            await r.sadd(f"series:{series_id}:games", game_id)
            await r.set(f"game_series:{game_id}", series_id)

            # Publish series update
            await _publish_series_update(series_id, r)

            # Start and wait for game
            task = await _start_game_internal(game_id, engine)
            await task

            # Increment completed
            await r.hincrby(f"series:{series_id}:meta", "completed_games", 1)

            if i < total_games - 1:
                await r.hset(f"series:{series_id}:meta", "status", "between_games")
                await _publish_series_update(series_id, r)
                await asyncio.sleep(settings.multi_game_delay)
                await r.hset(f"series:{series_id}:meta", "status", "running")

        await r.hset(f"series:{series_id}:meta", "status", "completed")
        await _publish_series_update(series_id, r)
    except Exception:
        logger.exception("Series %s crashed", series_id)
        await r.hset(f"series:{series_id}:meta", "status", "error")
        await _publish_series_update(series_id, r)
    finally:
        _series_tasks.pop(series_id, None)


@app.post(
    "/games/series",
    responses={
        400: {"description": "num_games must be >= 2"},
        403: {"description": "Invalid or missing admin secret"},
    },
)
async def create_series(
    req: CreateSeriesRequest,
    request: Request,
    _: Annotated[None, Depends(_require_admin_secret)],
):
    """Create and immediately start a multi-game series as a background task."""
    if req.num_games < 2:
        raise HTTPException(400, "num_games must be >= 2")

    series_id = f"series_{uuid.uuid4().hex[:8]}"
    r = await get_redis()

    await r.hset(
        f"series:{series_id}:meta",
        mapping={
            "name": req.name,
            "total_games": str(req.num_games),
            "status": "started",
            "created_at": datetime.now(UTC).isoformat(),
            "game_ids": json.dumps([]),
            "current_game_id": "",
            "completed_games": "0",
        },
    )
    await r.sadd("series_index", series_id)

    task = asyncio.create_task(
        _run_series(
            series_id, req.name, req.num_games, req.team_names, request.app.state
        )
    )
    _series_tasks[series_id] = task

    return {
        "series_id": series_id,
        "name": req.name,
        "total_games": req.num_games,
        "status": "started",
    }


@app.get("/series/{series_id}")
async def get_series(series_id: str):
    """Return metadata for a single series (progress, game IDs, status)."""
    r = await get_redis()
    meta = await r.hgetall(f"series:{series_id}:meta")
    if not meta:
        raise HTTPException(404, "Series not found")
    return {
        "series_id": series_id,
        "name": meta.get("name", ""),
        "total_games": int(meta.get("total_games", 0)),
        "completed_games": int(meta.get("completed_games", 0)),
        "current_game_id": meta.get("current_game_id", ""),
        "game_ids": json.loads(meta.get("game_ids", "[]")),
        "status": meta.get("status", ""),
        "created_at": meta.get("created_at", ""),
    }


@app.get("/series")
async def list_series():
    """Return all series with their metadata."""
    r = await get_redis()
    series_ids = await r.smembers("series_index")
    result = []
    for sid in series_ids:
        meta = await r.hgetall(f"series:{sid}:meta")
        if meta:
            result.append(
                {
                    "series_id": sid,
                    "name": meta.get("name", ""),
                    "total_games": int(meta.get("total_games", 0)),
                    "completed_games": int(meta.get("completed_games", 0)),
                    "current_game_id": meta.get("current_game_id", ""),
                    "game_ids": json.loads(meta.get("game_ids", "[]")),
                    "status": meta.get("status", ""),
                    "created_at": meta.get("created_at", ""),
                }
            )
    return {"series": result}


@app.get(
    "/series/{series_id}/spectate",
    responses={
        403: {"description": "Invalid or missing admin secret"},
        404: {"description": "Series not found"},
    },
)
async def spectate_series(
    series_id: str,
    request: Request,
    _: Annotated[None, Depends(_require_admin_secret)],
):
    """Open an SSE stream of series-level progress events. Admin only."""
    r = await get_redis()
    exists = await r.exists(f"series:{series_id}:meta")
    if not exists:
        raise HTTPException(404, "Series not found")
    return series_spectator_stream(series_id, request)


@app.get(
    "/series/{series_id}/stats",
    responses={404: {"description": "Series not found"}},
)
async def series_stats(series_id: str):
    """Return per-team scores and game IDs for a series."""
    r = await get_redis()
    exists = await r.exists(f"series:{series_id}:meta")
    if not exists:
        raise HTTPException(404, "Series not found")
    game_ids = await r.smembers(f"series:{series_id}:games")
    # Gather per-team stats from series scoreboard
    scores = await r.zrevrange(f"series:{series_id}:scoreboard", 0, -1, withscores=True)
    return {
        "series_id": series_id,
        "game_ids": list(game_ids),
        "team_scores": [{"team": team, "score": int(score)} for team, score in scores],
    }


@app.get(
    "/series/{series_id}/scoreboard",
    responses={404: {"description": "Series not found"}},
)
async def series_scoreboard(series_id: str):
    """Return the ranked scoreboard for a specific series."""
    r = await get_redis()
    exists = await r.exists(f"series:{series_id}:meta")
    if not exists:
        raise HTTPException(404, "Series not found")
    scores = await r.zrevrange(f"series:{series_id}:scoreboard", 0, -1, withscores=True)
    return {
        "standings": [{"team": team, "score": int(score)} for team, score in scores]
    }


# ---------------------------------------------------------------------------
# Scoreboard
# ---------------------------------------------------------------------------


@app.get("/scoreboard")
async def get_scoreboard():
    """Return the global scoreboard with all teams ranked by score (descending)."""
    r = await get_redis()
    scores = await r.zrevrange("scoreboard", 0, -1, withscores=True)
    return {
        "standings": [{"team": team, "score": int(score)} for team, score in scores]
    }
