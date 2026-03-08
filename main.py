import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: verify Redis connection
    r = await get_redis()
    await r.ping()
    logger.info("Connected to Redis")
    yield
    # Shutdown
    for task in _game_tasks.values():
        task.cancel()
    await close_redis()


app = FastAPI(title="Werewolf Host", version="0.1.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    team_name: str
    agent_url: str  # e.g. "ws://192.168.1.42:8080/ws"


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
    return {"agent_id": agent_id, "team_name": req.team_name, "status": "registered"}


@app.get("/api/teams")
async def list_teams():
    r = await get_redis()
    teams = await r.hgetall("teams")
    return {"teams": [{"team_name": k, "agent_url": v} for k, v in teams.items()]}


@app.delete("/api/teams/{team_name}")
async def unregister_team(team_name: str):
    r = await get_redis()
    removed = await r.hdel("teams", team_name)
    if not removed:
        raise HTTPException(404, "Team not found")
    return {"status": "removed"}


# ---------------------------------------------------------------------------
# Game lifecycle
# ---------------------------------------------------------------------------

@app.post("/api/games")
async def create_game(req: CreateGameRequest):
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

    game_id = f"game_{uuid.uuid4().hex[:8]}"
    players = []
    for team_name, agent_url in selected.items():
        agent_id = f"agent_{team_name.lower().replace(' ', '_')}"
        players.append(Player(id=agent_id, team=team_name, ws_url=agent_url))

    narrator = Narrator()
    engine = GameEngine(game_id, players, narrator)
    _games[game_id] = engine

    # Store game metadata in Redis
    await r.set(
        f"game:{game_id}:meta",
        json.dumps({
            "game_id": game_id,
            "teams": list(selected.keys()),
            "status": "created",
        }),
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
        "standings": [
            {"team": team, "score": int(score)} for team, score in scores
        ]
    }
