# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI Werewolf Hackathon Host — a FastAPI server that moderates Werewolf (social deduction) games between AI agents. The host connects to team agents via WebSocket, manages game phases (night → morning → discussion → voting), enforces rules/rate limits, and generates dramatic narration via an LLM.

## Commands

```bash
# Run the server (requires Redis running)
PYTHONPATH=src uvicorn main:app --host 0.0.0.0 --port 8000

# Run with Docker Compose (starts Redis + host)
docker compose up

# Install dependencies
pip install -e ".[dev]"

# Lint (ruff)
ruff check src/ tests/        # check for errors
ruff check --fix src/ tests/  # auto-fix what it can
ruff format src/ tests/        # format code

# Run tests
pytest
pytest tests/test_specific.py -k "test_name"
```

### Verify after changes

Always run both lint and tests after making code changes:
```bash
ruff check src/ tests/ && ruff format --check src/ tests/ && pytest
```

## Environment Variables

All prefixed with `WW_`:
- `WW_REDIS_URL` — Redis connection (default: `redis://localhost:6379/0`)
- `WW_OPENAI_API_KEY` — API key for narrator LLM
- `WW_OPENAI_BASE_URL` — Custom base URL (e.g., LiteLLM gateway)
- `WW_NARRATOR_MODEL` — Model name (default: `gpt-5-mini`)

## Architecture

**Entry point:** `src/main.py` — FastAPI app with REST endpoints for team registration, game creation/start, status, spectating (SSE), and scoreboard. Games are tracked in-memory (`_games` dict) and run as `asyncio.Task`s.

**Core modules in `src/app/`:**

- **`engine.py`** — `GameEngine` orchestrates the full game loop: role assignment → night phase (wolf voting) → morning announcement → discussion → banishment voting (with runoff) → win check. Uses `_collect_messages_for()` to process agent messages within timed windows.
- **`ws_manager.py`** — `ConnectionManager` holds inbound WebSocket connections from agents. Agents register via POST (receiving a token), then connect to the host's `/ws/agent` endpoint, passing their token in an `Authorization: Bearer <token>` header. Only one active WebSocket per team is allowed; duplicate connects are closed with code 4002. The manager waits for agents to connect, then routes messages through two logical channels: public (all players) and wolf-only. Incoming messages are deserialized via Pydantic discriminated unions and queued.
- **`narrator.py`** — `Narrator` uses the OpenAI SDK (pointed at any compatible API) to generate dramatic narration for game events. Returns empty strings if no API key is configured.
- **`spectator.py`** — SSE streaming endpoint backed by Redis pub/sub. Game events are published to `game:{id}:events` channels.
- **`rate_limiter.py`** — In-memory per-phase rate limiting: max messages, cooldown between messages, message length. Reset each discussion phase.
- **`redis.py`** — Async Redis connection pool (singleton). Used for team registry, game metadata, pub/sub for spectators, and scoreboard.
- **`config.py`** — `Settings` (pydantic-settings) with all timing, rate limit, and connection parameters. Also contains `wolves_for_player_count()` scaling table (5-7 players → 1 wolf, 8-10 → 2, 11+ → 3).

**Models (`src/app/models/`):**
- **`game.py`** — `Player`, `GameState`, `Role`, `Phase`, `Elimination` enums/models. `GameState` has computed properties for alive players/wolves/villagers and `check_winner()`.
- **`messages.py`** — All WebSocket message types as Pydantic models with `type` discriminator. Host→Agent messages (`GameStartMessage`, `PhaseChangeMessage`, etc.) and Agent→Host messages (`AgentChatMessage`, `AgentBanishmentVote`, `AgentNightVote`, etc.) as discriminated unions.

## Key Design Decisions

- Agents dial IN to the host (agents are pure WS clients; the host accepts inbound connections at `/ws/agent`). Registration returns a token used to authenticate the WebSocket.
- All messages use a `type` field as a Pydantic discriminated union discriminator
- `ChatBroadcast` and `WolfChatBroadcast` use `Field(alias="from")` for the sender field since `from` is a Python keyword
- Game state is in-memory; Redis is used for team registry, pub/sub, and scoreboard
- Narrator gracefully degrades to empty strings if no LLM API key is set
- Requires Python 3.12+ (uses `StrEnum`, `X | Y` union syntax)
- Game rules spec is in `werewolf_game.md`
