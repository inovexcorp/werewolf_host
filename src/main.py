import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import websockets
from fastapi import FastAPI, HTTPException, Request
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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    agent_url: str  # e.g. "ws://192.168.1.42:8080/ws"
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


@app.post("/api/register")
async def register_team(req: RegisterRequest):
    r = await get_redis()
    await r.hset("teams", req.team_name, req.agent_url)
    agent_id = f"agent_{req.team_name.lower().replace(' ', '_')}"

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
        "avatar_url": f"/{avatar_path}",
        "status": "registered",
    }


@app.get("/api/teams")
async def list_teams():
    r = await get_redis()
    teams = await r.hgetall("teams")
    avatars = await r.hgetall("team_avatars")
    default = default_avatar_path()
    return {
        "teams": [
            {
                "team_name": k,
                "agent_url": v,
                "avatar_url": f"/{avatars.get(k, default)}",
            }
            for k, v in teams.items()
        ]
    }


@app.delete("/api/teams/{team_name}")
async def unregister_team(team_name: str):
    r = await get_redis()
    removed = await r.hdel("teams", team_name)
    if not removed:
        raise HTTPException(404, "Team not found")
    return {"status": "removed"}


# ---------------------------------------------------------------------------
# Health & status checks
# ---------------------------------------------------------------------------


@app.get("/api/health")
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


@app.get("/api/teams/{team_name}/status")
async def team_status(team_name: str):
    r = await get_redis()
    agent_url = await r.hget("teams", team_name)
    if agent_url is None:
        raise HTTPException(404, "Team not found")

    avatars = await r.hgetall("team_avatars")
    avatar_path = avatars.get(team_name, default_avatar_path())

    return {
        "registered": True,
        "team_name": team_name,
        "agent_url": agent_url,
        "avatar_url": f"/{avatar_path}",
    }


@app.post("/api/teams/{team_name}/check")
async def check_team_connectivity(team_name: str):
    r = await get_redis()
    agent_url = await r.hget("teams", team_name)
    if agent_url is None:
        raise HTTPException(404, "Team not found")

    try:
        async with websockets.connect(agent_url, open_timeout=5):
            pass
        return {
            "team_name": team_name,
            "agent_url": agent_url,
            "reachable": True,
        }
    except Exception as e:
        return {
            "team_name": team_name,
            "agent_url": agent_url,
            "reachable": False,
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Game lifecycle
# ---------------------------------------------------------------------------


@app.post("/api/games")
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
    for team_name, agent_url in selected.items():
        agent_id = f"agent_{team_name.lower().replace(' ', '_')}"
        avatar_path = avatars.get(team_name, default)
        players.append(
            Player(
                id=agent_id,
                team=team_name,
                ws_url=agent_url,
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


@app.post("/api/games/{game_id}/start")
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


@app.get("/api/games/{game_id}")
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


@app.get("/api/games/{game_id}/spectate")
async def spectate_game(game_id: str, request):
    if game_id not in _games:
        raise HTTPException(404, "Game not found")
    return spectator_stream(game_id, request)


# ---------------------------------------------------------------------------
# Scoreboard
# ---------------------------------------------------------------------------


@app.get("/api/scoreboard")
async def get_scoreboard():
    r = await get_redis()
    scores = await r.zrevrange("scoreboard", 0, -1, withscores=True)
    return {
        "standings": [{"team": team, "score": int(score)} for team, score in scores]
    }
