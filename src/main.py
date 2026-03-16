import asyncio
import json
import logging
import secrets
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi import WebSocket as FastAPIWebSocket
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.avatar import default_avatar_path, ensure_avatar_dir, process_avatar
from app.config import settings
from app.engine import GameEngine
from app.models.game import Player
from app.narrator import Narrator
from app.redis import close_redis, get_redis
from app.spectator import spectator_stream
from app.ws_manager import agent_connected, agent_disconnected, get_connected_agents

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class _HealthCheckFilter(logging.Filter):
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


def _redact_key(key: str) -> str:
    if not key:
        return "(not set)"
    return f"{key[:4]}...{key[-4:]}" if len(key) > 8 else "****"


def _log_settings_summary():
    logger.info("=== Werewolf Host Configuration ===")
    logger.info("  Redis URL:       %s", settings.redis_url)
    logger.info("  Narrator model:  %s", settings.narrator_model)
    logger.info("  OpenAI base URL: %s", settings.openai_base_url)
    logger.info("  OpenAI API key:  %s", _redact_key(settings.openai_api_key))
    logger.info("  Intro duration:  %ds", settings.introduction_duration)
    logger.info("  Night duration:  %ds", settings.night_duration)
    logger.info("  Discussion dur:  %ds", settings.discussion_duration)
    logger.info("  Voting duration: %ds", settings.voting_duration)
    if not settings.openai_api_key:
        logger.warning("No OpenAI API key set — narrator features will be disabled")
    logger.info("===================================")


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    for task in _game_tasks.values():
        task.cancel()
    await close_redis()


app = FastAPI(title="Werewolf Host", version="0.1.0", lifespan=lifespan)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
app.mount("/static", StaticFiles(directory=_PROJECT_ROOT / "static"), name="static")


@app.get("/")
async def root():
    return RedirectResponse(url="/docs")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    team_name: str
    avatar: str | None = None  # optional base64-encoded image


class CreateGameRequest(BaseModel):
    team_names: list[str] | None = None  # None = use all registered teams
    player_count: int | None = None


class GameStatusResponse(BaseModel):
    game_id: str
    phase: str
    round: int
    alive_players: list[str]
    winner: str | None = None


# ---------------------------------------------------------------------------
# Team registration
# ---------------------------------------------------------------------------


@app.post("/register", responses={400: {"description": "Invalid avatar"}})
async def register_team(req: RegisterRequest):
    r = await get_redis()
    token = secrets.token_urlsafe(32)

    # On re-registration, delete old reverse mapping
    old_token = await r.hget("teams", req.team_name)
    if old_token:
        await r.hdel("team_tokens", old_token)

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


@app.delete("/teams/{team_name}", responses={404: {"description": "Team not found"}})
async def unregister_team(team_name: str):
    r = await get_redis()
    old_token = await r.hget("teams", team_name)
    removed = await r.hdel("teams", team_name)
    if not removed:
        raise HTTPException(404, "Team not found")
    if old_token:
        await r.hdel("team_tokens", old_token)
    return {"status": "removed"}


# ---------------------------------------------------------------------------
# Health & status checks
# ---------------------------------------------------------------------------


@app.get("/health")
async def health_check():
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
    responses={404: {"description": "Team not found"}},
)
async def team_status(team_name: str):
    r = await get_redis()
    token = await r.hget("teams", team_name)
    if token is None:
        raise HTTPException(404, "Team not found")

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
    responses={404: {"description": "Team not found"}},
)
async def team_stats(team_name: str):
    r = await get_redis()
    token = await r.hget("teams", team_name)
    if token is None:
        raise HTTPException(404, "Team not found")

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
async def agent_ws_endpoint(websocket: FastAPIWebSocket, token: str = Query(...)):
    r = await get_redis()
    team_name = await r.hget("team_tokens", token)
    if not team_name:
        await websocket.close(code=4001, reason="Invalid token")
        return
    await websocket.accept()
    logger.info("Agent %s connected via WebSocket", team_name)
    agent_connected(team_name, websocket)
    try:
        while True:
            await asyncio.sleep(3600)
    except Exception:
        logger.info("Agent %s WebSocket closed", team_name)
    finally:
        agent_disconnected(team_name)


# ---------------------------------------------------------------------------
# Game lifecycle
# ---------------------------------------------------------------------------


@app.post(
    "/games",
    responses={400: {"description": "No teams / missing teams / too few teams"}},
)
async def create_game(req: CreateGameRequest, request: Request):
    r = await get_redis()
    all_teams = await r.hgetall("teams")

    if not all_teams:
        raise HTTPException(400, "No teams registered")

    if req.team_names:
        missing = [t for t in req.team_names if t not in all_teams]
        if missing:
            raise HTTPException(400, f"Teams not found: {missing}")
        selected = {t: all_teams[t] for t in req.team_names}
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

    backstory = getattr(request.app.state, "host_backstory", "")
    narrator = Narrator(host_backstory=backstory)
    engine = GameEngine(game_id, players, narrator, host_backstory=backstory)
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

    return {"game_id": game_id, "players": len(players), "status": "created"}


@app.post(
    "/games/{game_id}/start",
    responses={
        400: {"description": "Game already running"},
        404: {"description": "Game not found"},
        502: {"description": "Failed to connect to agents"},
    },
)
async def start_game(game_id: str):
    engine = _games.get(game_id)
    if not engine:
        raise HTTPException(404, "Game not found")
    if game_id in _game_tasks:
        raise HTTPException(400, "Game already running")

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

    task = asyncio.create_task(_run_game())
    _game_tasks[game_id] = task

    return {"game_id": game_id, "status": "started"}


@app.get("/games")
async def list_games():
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


@app.get("/games/{game_id}", responses={404: {"description": "Game not found"}})
async def get_game_status(game_id: str) -> GameStatusResponse:
    engine = _games.get(game_id)
    if not engine:
        raise HTTPException(404, "Game not found")
    return GameStatusResponse(
        game_id=engine.state.game_id,
        phase=engine.state.phase,
        round=engine.state.round,
        alive_players=engine.state.alive_player_ids,
        winner=engine.state.winner,
    )


def _require_spectator_secret(request: Request) -> None:
    secret = settings.spectator_secret
    if secret:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {secret}":
            raise HTTPException(403, "Invalid or missing spectator secret")


@app.get(
    "/games/{game_id}/players",
    responses={
        403: {"description": "Invalid or missing spectator secret"},
        404: {"description": "Game not found"},
    },
)
async def get_game_players(
    game_id: str,
    _: Annotated[None, Depends(_require_spectator_secret)],
):
    engine = _games.get(game_id)
    if not engine:
        raise HTTPException(404, "Game not found")
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
        403: {"description": "Invalid or missing spectator secret"},
        404: {"description": "Game not found"},
    },
)
async def spectate_game(
    game_id: str,
    request: Request,
    _: Annotated[None, Depends(_require_spectator_secret)],
):
    if game_id not in _games:
        raise HTTPException(404, "Game not found")
    return spectator_stream(game_id, request)


# ---------------------------------------------------------------------------
# Scoreboard
# ---------------------------------------------------------------------------


@app.get("/scoreboard")
async def get_scoreboard():
    r = await get_redis()
    scores = await r.zrevrange("scoreboard", 0, -1, withscores=True)
    return {
        "standings": [{"team": team, "score": int(score)} for team, score in scores]
    }
