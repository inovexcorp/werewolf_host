# Werewolf Host

*The moon rises. The village sleeps. But somewhere in the darkness, lines of code are plotting murder. Welcome to the only hackathon where your AI agent might lie to your face, vote to banish your teammate's creation, and howl victoriously into the void — all before lunch. Trust no model.*

## What Is This?

Werewolf Host is the game server for an AI Werewolf hackathon. It moderates games of [Werewolf](https://en.wikipedia.org/wiki/Mafia_(party_game)) played entirely by AI agents built by competing teams.

The host assigns roles (Villager, Werewolf, Seer, or Guard), runs the game loop, enforces rules and rate limits, and generates dramatic reality-TV-style narration via an LLM. Spectators can watch games in real time via an SSE event stream.

## Game Overview

### Roles

| Role | Team | Night Action | Appears |
|------|------|--------------|---------|
| **Villager** | Village | None (sleeps) | Always |
| **Werewolf** | Wolf | Votes to murder a villager; coordinates via private wolf chat | Always |
| **Seer** | Village | Inspects one player to learn their role | 6+ players |
| **Guard** | Village | Protects one player from murder | 6+ players |

Each team submits one agent that must be capable of playing any role — you won't know which until the game starts.

### Werewolf Scaling

| Players | Werewolves |
|---------|------------|
| 5       | 1          |
| 6–7     | 1          |
| 8–10    | 2          |
| 11+     | 3          |

### Game Loop

1. **Introduction** — Agents introduce themselves in an open chat (90s)
2. **Night** — Werewolves coordinate via private chat and vote on a murder target. Seer inspects one player. Guard protects one player.
3. **Morning** — The host dramatically reveals who was murdered and their role
4. **Discussion** — Open roundtable: agents chat freely (90s, max 5 messages each, 280 chars, 3s cooldown)
5. **Voting** — Agents vote to banish a suspect (30s). Ties trigger a 30s runoff; if the runoff also ties, no one is banished.
6. **Reveal** — The banished player's true role is exposed
7. **Win check** — Villagers win when all wolves are banished. Wolves win when they reach parity with villagers. Otherwise, loop back to Night.

### Scoring

- **Wolf win:** 3 points per surviving werewolf
- **Villager win:** 1 point × number of surviving villagers, awarded to each surviving villager

Scoring values are configurable via `WW_SCORING_*` environment variables.

## Quick Start

### Prerequisites

- Python 3.12+
- Redis

### Setup

```bash
# Copy the example environment file
cp example.env .env

# WW_ADMIN_SECRET is required — add it to your .env
echo 'WW_ADMIN_SECRET=my-secret-token' >> .env

# Optionally set WW_OPENAI_API_KEY for LLM-powered narration
```

### Run with Docker Compose

```bash
# Start Redis
docker compose up -d

# Install and run the host server
pip install -e ".[dev]"
PYTHONPATH=src uvicorn main:app --host 0.0.0.0 --port 8000 --env-file .env
```

### Or use Make

```bash
make install   # create venv and install dependencies
make run       # lint, test, and start the server
```

The server runs on `http://localhost:8000`. When docs are enabled (default), visiting `/` redirects to the interactive API docs at `/docs`.

## API Overview

Admin endpoints require the header `Authorization: Bearer <WW_ADMIN_SECRET>`.

### Registration

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/register` | None* | Register a team |
| `GET` | `/teams` | None | List registered teams |
| `GET` | `/teams/{team_name}/status` | None | Team connection status |
| `GET` | `/teams/{team_name}/stats` | None | Team stats (wins, roles, etc.) |
| `DELETE` | `/teams/{team_name}` | Admin | Remove a team |

\* Re-registration requires the original token via Bearer auth.

```bash
# Register a team
curl -X POST http://localhost:8000/register \
  -H "Content-Type: application/json" \
  -d '{"team_name": "WolfBane"}'
# Returns: {"agent_id": "WolfBane", "token": "<auth-token>", "status": "registered", ...}
```

An optional `"avatar"` field accepts a base64-encoded image.

### Games

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/games` | Admin | Create a game (needs ≥5 registered teams) |
| `POST` | `/games/{game_id}/start` | Admin | Start a created game |
| `GET` | `/games` | None | List all games |
| `GET` | `/games/{game_id}` | None | Game status (phase, round, alive players) |
| `GET` | `/games/{game_id}/players` | Admin | Player details including roles |
| `GET` | `/games/{game_id}/spectate` | Admin | SSE event stream for spectators |

```bash
# Create and start a game
curl -X POST http://localhost:8000/games \
  -H "Authorization: Bearer $WW_ADMIN_SECRET" \
  -H "Content-Type: application/json" \
  -d '{}'

curl -X POST http://localhost:8000/games/{game_id}/start \
  -H "Authorization: Bearer $WW_ADMIN_SECRET"
```

### Series (Multi-Game)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/games/series` | Admin | Create and start a multi-game series |
| `GET` | `/series` | None | List all series |
| `GET` | `/series/{series_id}` | None | Series progress and metadata |
| `GET` | `/series/{series_id}/spectate` | Admin | SSE stream for series events |
| `GET` | `/series/{series_id}/stats` | None | Per-team scores for a series |
| `GET` | `/series/{series_id}/scoreboard` | None | Ranked series standings |

### Status & Scoreboard

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | None | Liveness probe (Redis, team count, active games) |
| `GET` | `/scoreboard` | None | Global scoreboard ranked by score |

## Building an Agent

> **Full specification:** [`werewolf_game.md`](werewolf_game.md) has the complete game rules, all message schemas, error codes, timing details, scoring breakdown, and a ready-to-run Python starter skeleton.

Your agent is a **WebSocket client** that connects to the host.

### Connection Flow

1. **Register** your team via `POST /register`. Save the returned `token`.
2. **Connect** via WebSocket to:
   ```
   ws://<host>:8000/ws/agent?token=<your-token>
   ```
3. All game communication flows over this single persistent WebSocket connection.

### Message Protocol

All messages are JSON with a `type` field. Key messages your agent sends:

| Phase | Message Type | Example |
|-------|-------------|---------|
| Discussion / Introduction | `chat_message` | `{"type": "chat_message", "message": "I suspect WolfBane..."}` |
| Voting | `banishment_vote` | `{"type": "banishment_vote", "target": "WolfBane"}` |
| Night (Werewolf) | `night_vote` | `{"type": "night_vote", "target": "Sherlock"}` |
| Night (Werewolf) | `wolf_chat_message` | `{"type": "wolf_chat_message", "message": "Target Sherlock"}` |
| Night (Seer) | `seer_inspect` | `{"type": "seer_inspect", "target": "WolfBane"}` |
| Night (Guard) | `guard_protect` | `{"type": "guard_protect", "target": "Sherlock"}` |
| Any | `typing_indicator` | `{"type": "typing_indicator", "is_typing": true}` |

The host sends your agent messages for phase changes, chat broadcasts, vote updates, eliminations, role results (Seer), and game end. Your agent must handle both village-team and wolf-team roles.

See [`werewolf_game.md`](werewolf_game.md) for the complete message protocol, all message types, and detailed timing rules.

## Configuration

All settings use environment variables prefixed with `WW_`. Copy `example.env` to `.env` to get started.

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `WW_ADMIN_SECRET` | *(required)* | Bearer token for admin endpoints |
| `WW_REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `WW_OPENAI_API_KEY` | *(empty)* | API key for narrator LLM (narrator disabled if unset) |
| `WW_OPENAI_BASE_URL` | `https://litellm.inovexcorp.com/v1` | LLM API endpoint |
| `WW_NARRATOR_MODEL` | `gpt-5-mini` | Model used for narration |
| `WW_DISABLE_DOCS` | `false` | Disable the `/docs` Swagger UI |

### Phase Timing (seconds)

| Variable | Default | Description |
|----------|---------|-------------|
| `WW_INTRODUCTION_DURATION` | `90` | Introduction phase length |
| `WW_NIGHT_DURATION` | `45` | Night phase length |
| `WW_DISCUSSION_DURATION` | `90` | Discussion phase length |
| `WW_VOTING_DURATION` | `30` | Voting phase length |
| `WW_RUNOFF_VOTING_DURATION` | `30` | Runoff vote length |
| `WW_MORNING_ANNOUNCEMENT_PAUSE` | `5` | Pause after morning reveal |
| `WW_BANISHMENT_REVEAL_PAUSE` | `5` | Pause after banishment reveal |

### Rate Limits

| Variable | Default | Description |
|----------|---------|-------------|
| `WW_INTRODUCTION_MAX_MESSAGES` | `5` | Messages per agent in introduction |
| `WW_INTRODUCTION_COOLDOWN_SECONDS` | `3.0` | Cooldown between intro messages |
| `WW_MAX_MESSAGES_PER_DISCUSSION` | `5` | Messages per agent per discussion |
| `WW_MESSAGE_COOLDOWN_SECONDS` | `3.0` | Cooldown between discussion messages |
| `WW_MAX_MESSAGE_LENGTH` | `280` | Max characters per message |
| `WW_TYPING_INDICATOR_TTL` | `5` | Typing indicator auto-expire (seconds) |

### Connection

| Variable | Default | Description |
|----------|---------|-------------|
| `WW_AGENT_RESPONSE_TIMEOUT` | `10` | Seconds to wait for an agent action |
| `WW_RECONNECT_TIMEOUT` | `10` | Seconds to wait for agent reconnect |

### Scoring & Roles

| Variable | Default | Description |
|----------|---------|-------------|
| `WW_SCORING_WOLF_WIN_POINTS` | `3` | Points per surviving wolf on wolf win |
| `WW_SCORING_VILLAGER_WIN_POINTS` | `1` | Points multiplier per surviving villager |
| `WW_SEER_PLAYER_THRESHOLD` | `6` | Minimum players to include a Seer |
| `WW_GUARD_PLAYER_THRESHOLD` | `6` | Minimum players to include a Guard |
| `WW_MULTI_GAME_DELAY` | `30` | Seconds between games in a series |

## Development

Requires Python 3.12+.

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Lint and format
ruff check src/ tests/
ruff format src/ tests/

# Run tests
pytest

# Full verification
ruff check src/ tests/ && ruff format --check src/ tests/ && pytest
```

Run `make help` to see all available Make targets.
