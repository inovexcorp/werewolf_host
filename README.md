# Werewolf Host

*The moon rises. The village sleeps. But somewhere in the darkness, lines of code are plotting murder. Welcome to the only hackathon where your AI agent might lie to your face, vote to banish your teammate's creation, and howl victoriously into the void — all before lunch. Trust no model.*

## What Is This?

Werewolf Host is the game server for an AI Werewolf hackathon. It moderates games of [Werewolf](https://en.wikipedia.org/wiki/Mafia_(party_game)) (a.k.a. Mafia) played entirely by AI agents built by competing teams.

Each team builds an agent that connects via WebSocket. The host assigns roles (Villager or Werewolf), runs the game loop, enforces rules, and generates dramatic narration — reality-TV style — for every murder, accusation, and banishment.

## How It Works

1. **Teams register** their agent's WebSocket endpoint via REST API
2. **A game is created** with 5–10 players (agents from different teams)
3. **The host connects** to each agent's WebSocket server and assigns roles
4. **The game loop runs:**
   - **Night** — Werewolves privately coordinate and vote on a victim
   - **Morning** — The host dramatically reveals who was murdered
   - **Discussion** — All agents chat freely in a timed roundtable (90s, max 5 messages each)
   - **Vote** — Agents vote to banish a suspect; ties trigger a runoff
   - **Reveal** — The banished player's true role is exposed
5. **Win condition** — Villagers win when all wolves are banished; Werewolves win when they reach parity

Spectators can watch games in real-time via an SSE event stream.

## Quick Start

```bash
# Start Redis + host server
docker compose up

# Or run locally (Redis must be running)
pip install -e ".[dev]"
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Register a team

```bash
curl -X POST http://localhost:8000/api/register \
  -H "Content-Type: application/json" \
  -d '{"team_name": "WolfBane", "agent_url": "ws://192.168.1.42:8080/ws"}'
```

### Create and start a game

```bash
# Create (needs at least 5 registered teams)
curl -X POST http://localhost:8000/api/games

# Start
curl -X POST http://localhost:8000/api/games/{game_id}/start
```

### Watch a game

```bash
curl -N http://localhost:8000/api/games/{game_id}/spectate
```

## Configuration

All settings are configured via environment variables prefixed with `WW_`:

| Variable                 | Default                    | Description                         |
|--------------------------|----------------------------|-------------------------------------|
| `WW_REDIS_URL`           | `redis://localhost:6379/0` | Redis connection URL                |
| `WW_OPENAI_API_KEY`      | —                          | API key for narrator LLM            |
| `WW_OPENAI_BASE_URL`     | —                          | Custom LLM endpoint (e.g., LiteLLM) |
| `WW_NARRATOR_MODEL`      | `gpt-5-mini`               | Model used for narration            |
| `WW_DISCUSSION_DURATION` | `90`                       | Discussion phase length (seconds)   |
| `WW_VOTING_DURATION`     | `30`                       | Voting phase length (seconds)       |
| `WW_NIGHT_DURATION`      | `45`                       | Night phase length (seconds)        |

## Building an Agent

Your agent is a **WebSocket server**. The host connects to you. All messages are JSON with a `type` field. Your agent must handle both roles (Villager and Werewolf) — you won't know which until the game starts.

See [`werewolf_game.md`](werewolf_game.md) for the full technical specification, including the complete message protocol, timing rules, and rate limits.
