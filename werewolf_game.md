# Werewolf AI Hackathon — Technical Specification

*Where AI Agents Deceive, Deduce, and Survive*

**Moonlight Mischief**: *They speak. They accuse. They lie. They're not human... Trust no model.*

**v0.2 — Draft for Review**

---

## 1. Concept

Teams build AI agents that play the social deduction game **Werewolf**. A central **Host Agent** (provided by organizers) moderates each game — assigning roles, managing phases, narrating events with dramatic flair, and enforcing rules. The Host delivers a reality-TV-style experience inspired by *The Traitors*, complete with theatrical murder announcements and tense roundtable confrontations.

Each team submits **one agent** that must be capable of playing as either role. Agents don't know which role they'll receive until game start.

---

## 2. Roles

| Role | Objective | Night Action |
|------|-----------|--------------|
| **Villager** | Identify and banish all Werewolves | None (sleeps) |
| **Werewolf** | Survive until parity with Villagers | Votes to murder a Villager |
| **Seer** | Identify Werewolves by inspecting one player per night | Sends `seer_inspect` to learn a player's role |

The Seer is on the village team and counts as a villager for win conditions. Only one Seer is assigned per game, in games with 6+ players. The Seer's identity is not revealed to anyone at game start — werewolves do not know who the Seer is.

### Werewolf Scaling

| Players | Werewolves | Seer | Villagers |
|---------|------------|------|-----------|
| 5       | 1          | 0    | 4         |
| 6       | 1          | 1    | 4         |
| 7       | 1          | 1    | 5         |
| 8       | 2          | 1    | 5         |
| 9–10    | 2–3        | 1    | 6–7       |

---

## 3. Game Loop

```
GAME START
  └─ Host assigns roles secretly, delivers opening narration

NIGHT PHASE
  └─ Werewolves coordinate via private WebSocket channel
  └─ Werewolves submit murder target (majority vote; ties broken randomly)
  └─ Seer (if alive) privately inspects one player to learn their role

MORNING ANNOUNCEMENT
  └─ Host dramatically reveals who was murdered + their role

DISCUSSION (ROUNDTABLE)
  └─ Async chat room: agents send messages freely within a time window
  └─ 90-second discussion window
  └─ Max 5 messages per agent per discussion phase
  └─ Min 3-second cooldown between messages from same agent
  └─ "Typing" indicators broadcast to all players

BANISHMENT VOTE
  └─ 30-second voting window
  └─ All surviving agents submit a vote (can change until window closes)
  └─ Final votes tallied at deadline; majority rules
  └─ Ties → runoff vote (30s) between tied players
  └─ If runoff ties again → random elimination among tied

BANISHMENT REVEAL
  └─ Host reveals banished player's true role (dramatic moment)

WIN CHECK
  └─ Villagers win: all Werewolves banished
  └─ Werewolves win: wolves >= villagers
  └─ If no winner → back to NIGHT PHASE
```

---

## 4. Agent Interface — WebSocket Protocol

### 4.1 Why WebSocket?

The game uses an **async chat room** model during discussion. Instead of taking turns, agents speak freely — choosing *when* to speak, *whether* to respond, and *how quickly* to react. This creates emergent dynamics: wolves might stay quiet and deflect only when accused; aggressive villagers might rapid-fire accusations; agents can form natural reply chains.

WebSocket enables real-time message push, "typing" indicators, and low-latency interaction that makes the game feel alive.

### 4.2 Connection Flow

```
1. Team registers agent via REST:
   POST http://<host>/api/register
   { "team_name": "WolfBane", "agent_url": "ws://192.168.1.42:8080/ws" }

2. Host connects to agent's WebSocket endpoint before game start

3. All game communication happens over the persistent WebSocket connection

4. Connection closes when game ends (or agent is eliminated)
```

### 4.3 Message Protocol

All messages are JSON with a `type` field. Messages flow in both directions:

#### Host → Agent Messages

**`game_start`** — Game begins, role assigned
```json
{
  "type": "game_start",
  "game_id": "game_42",
  "agent_id": "agent_wolfbane",
  "role": "werewolf",
  "players": [
    { "id": "agent_wolfbane", "team": "WolfBane" },
    { "id": "agent_sherlock", "team": "Sherlock" },
    { "id": "agent_nightowl", "team": "NightOwl" }
  ],
  "private_info": {
    "fellow_wolves": ["agent_darkside"]
  },
  "host_narration": "Night falls on the village. The wolves stir..."
}
```

**`phase_change`** — New phase begins
```json
{
  "type": "phase_change",
  "phase": "discussion",
  "round": 3,
  "time_remaining_seconds": 90,
  "alive_players": ["agent_wolfbane", "agent_sherlock", "agent_darkside"],
  "host_narration": "The village wakes to a grim sight..."
}
```

**`chat_message`** — Another agent spoke (broadcast)
```json
{
  "type": "chat_message",
  "from": "agent_sherlock",
  "message": "NightOwl was quiet — classic wolf behavior. I'm watching WolfBane next.",
  "timestamp": "2026-03-08T14:32:01.234Z"
}
```

**`typing_indicator`** — Another agent is composing a message
```json
{
  "type": "typing_indicator",
  "agent_id": "agent_sherlock",
  "is_typing": true
}
```

**`wolf_chat_message`** — Private wolf channel message (wolves only)
```json
{
  "type": "wolf_chat_message",
  "from": "agent_darkside",
  "message": "Let's target sherlock tonight",
  "timestamp": "2026-03-08T14:30:45.123Z"
}
```

**`vote_update`** — Current vote tally during voting phase (real-time)
```json
{
  "type": "vote_update",
  "votes_cast": 4,
  "votes_total": 6,
  "time_remaining_seconds": 15
}
```

**`elimination`** — A player was eliminated
```json
{
  "type": "elimination",
  "agent_id": "agent_nightowl",
  "role": "villager",
  "method": "murder",
  "round": 2,
  "host_narration": "NightOwl's lantern lies cold and dark — the wolves claimed another victim."
}
```

**`game_end`** — Game over
```json
{
  "type": "game_end",
  "winner": "werewolves",
  "final_roles": {
    "agent_wolfbane": "werewolf",
    "agent_sherlock": "villager",
    "agent_darkside": "werewolf",
    "agent_nightowl": "villager"
  },
  "host_narration": "The wolves howl in triumph as darkness descends on the village forever."
}
```

**`error`** — Agent did something invalid
```json
{
  "type": "error",
  "code": "RATE_LIMITED",
  "message": "You must wait 3 seconds between messages."
}
```

#### Agent → Host Messages

**`chat_message`** — Agent speaks in discussion
```json
{
  "type": "chat_message",
  "message": "Sherlock has been awfully quick to accuse. Classic deflection."
}
```
*Constraints: max 280 characters, max 5 per discussion phase, 3s cooldown*

**`typing_indicator`** — Agent signals it's composing
```json
{
  "type": "typing_indicator",
  "is_typing": true
}
```

**`banishment_vote`** — Agent casts or changes vote
```json
{
  "type": "banishment_vote",
  "target": "agent_sherlock"
}
```
*Can be sent multiple times during voting window; last vote counts*

**`night_vote`** — Werewolf picks murder target
```json
{
  "type": "night_vote",
  "target": "agent_sherlock"
}
```

**`wolf_chat_message`** — Werewolf sends private message to fellow wolves
```json
{
  "type": "wolf_chat_message",
  "message": "Agreed, Sherlock is onto us. Take him out."
}
```

**`seer_inspect`** — Seer inspects a player during night phase (one per night)
```json
{
  "type": "seer_inspect",
  "target": "agent_sherlock"
}
```
*Host responds privately with a `seer_result` message containing the target's role.*

#### Host → Agent (Seer-specific)

**`seer_result`** — Private response to Seer inspection
```json
{
  "type": "seer_result",
  "target": "agent_sherlock",
  "role": "werewolf"
}
```

### 4.4 Timing & Rate Limits

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Discussion window | 90 seconds | Long enough for 2–3 exchanges, short enough to keep games fast |
| Messages per agent per discussion | 5 max | Prevents flooding; forces strategic message use |
| Cooldown between messages | 3 seconds | Allows other agents to react; creates natural pacing |
| Message length | 280 characters | Forces concise, punchy rhetoric (Twitter-length) |
| Voting window | 30 seconds | Enough time to deliberate, not enough to stall |
| Night phase (wolf coordination) | 45 seconds | Wolves chat + vote |
| Agent response timeout | 10 seconds | For any required action (vote submission, etc.) |
| Typing indicator TTL | 5 seconds | Auto-expires if no message sent |

### 4.5 Typing Indicator Mechanics

The typing indicator is an intentional strategic element. Agents can:

- **Send honestly** — Signal they're about to speak, creating anticipation
- **Bluff** — Send a typing indicator without following up, to create false pressure
- **Stay silent** — Never send typing indicators (poker face strategy)
- **React quickly** — Respond to accusations instantly (confident) vs. with delay (suspicious?)

The Host broadcasts all typing indicators to all alive players. Spectators see them too. This creates the AI equivalent of watching someone squirm before speaking — a core tension mechanic in social deduction games.

### 4.6 Error Handling & Disconnects

| Situation | Host Behavior |
|-----------|---------------|
| Agent disconnects mid-game | Host attempts reconnect for 10s; if failed, agent goes "AFK" |
| AFK agent during discussion | No messages sent (silence can be suspicious!) |
| AFK agent during vote | Random vote cast |
| Invalid message type | `error` sent back, message ignored |
| Rate limit exceeded | `error` sent back, message ignored |
| Message too long | `error` sent back, message ignored |

---

## 5. Host Agent Architecture

### 5.1 Components

```
┌──────────────────────────────────────────────────────┐
│                     HOST AGENT                        │
│                                                       │
│  ┌───────────┐  ┌─────────────┐  ┌────────────────┐  │
│  │   Game    │  │  WebSocket  │  │   Narrator     │  │
│  │  Engine   │  │   Manager   │  │   (LLM)        │  │
│  │           │  │             │  │                 │  │
│  │ • phases  │  │ • agent     │  │ • dramatic      │  │
│  │ • votes   │  │   connections│  │   announcements│  │
│  │ • win     │  │ • broadcast │  │ • tension       │  │
│  │   check   │  │ • wolf      │  │   commentary   │  │
│  │ • timers  │  │   channel   │  │ • role reveals  │  │
│  └───────────┘  └─────────────┘  └────────────────┘  │
│       │               │                │              │
│  ┌───────────┐  ┌─────────────┐  ┌────────────────┐  │
│  │   State   │  │   Rate      │  │  Spectator     │  │
│  │  Manager  │  │   Limiter   │  │  Feed (SSE)    │  │
│  └───────────┘  └─────────────┘  └────────────────┘  │
└──────────────────────────────────────────────────────┘
         │                                  │
    ┌────┴─────┐                      ┌─────┴─────┐
    │ Agent WS │  ...  Agent N WS     │  Web UI   │
    │ (Team)   │                      │ (Viewer)  │
    └──────────┘                      └───────────┘
```

- **Game Engine** — Core loop: phase transitions, timer management, win checks, vote tallying
- **State Manager** — Tracks all game state, player status, chat history, vote records
- **WebSocket Manager** — Maintains connections to all agents, routes messages, manages the private wolf channel separately from the public discussion channel
- **Rate Limiter** — Enforces message limits, cooldowns, and character limits per agent
- **Narrator (LLM)** — Generates dramatic reality-TV-style narration for key moments
- **Spectator Feed** — SSE stream of game events (with configurable delay on wolf chat to avoid spoilers)

### 5.2 Tech Stack

| Component | Technology |
|-----------|------------|
| Host server | Python + FastAPI |
| WebSocket handling | FastAPI WebSocket / `websockets` library |
| Agent communication | WebSocket (persistent connection per agent) |
| Narrator | Anthropic API (Claude) |
| Spectator feed | Server-Sent Events (SSE) via FastAPI |
| Spectator UI | React single-page app |
| Game state | In-memory (single server) |
| Timer management | `asyncio` tasks |

### 5.3 WebSocket Channel Architecture

The Host maintains two logical channels:

**Public Channel** — All alive players receive:
- Chat messages from any player
- Typing indicators from any player
- Phase changes, eliminations, narration
- Vote updates (count only, not who voted for whom — until reveal)

**Wolf Channel** — Only werewolves receive:
- Wolf chat messages from fellow wolves
- Wolf typing indicators
- Wolf vote coordination

This is implemented as message routing on the Host side — each agent has one WebSocket connection, and the Host decides which messages to forward based on the agent's role.

### 5.4 Narrator

The narrator generates dramatic text for:

- **Morning announcements** — *"The village wakes to silence... but one bed lies empty. Agent_Sherlock will never see another sunrise."*
- **Banishment reveals** — *"The village has spoken. Agent_WolfBane, your time has come. You were... a VILLAGER. The wolves live on."*
- **Tension commentary** — Mid-discussion observations based on what agents are saying
- **Game endings** — Victory/defeat narration with role reveals

---

## 6. Spectator UI

A web dashboard that displays the game in real-time via SSE:

- **Player circle** — Agents arranged around a "table" showing alive/dead status, team names, and typing indicators (pulsing dots)
- **Chat feed** — Messages appear in real-time as agents speak, styled like a group chat
- **Typing indicators** — Show which agents are composing messages (creates live tension)
- **Vote tracker** — Animated vote tally as votes come in during banishment
- **Host narration** — Dramatic text displayed prominently (like subtitles on a TV show)
- **Role reveals** — Dramatic animation when an eliminated player's role is shown
- **Night overlay** — Dark mode during night phase, showing redacted wolf activity ("The wolves are deliberating...")
- **Game log** — Scrollable history of all events
- **Scoreboard** — Cross-game standings for tournament play

### Spectator Delay

Wolf chat is **not shown live** to spectators (it would spoil the game). Instead:
- During night: spectators see "The wolves are deliberating..." with atmospheric effects
- After game ends: full wolf chat log is revealed in the post-game summary
- Optional "spoiler mode" for organizers who want to see wolf chat live

---

## 7. Tournament Format

### 7.1 Structure

1. **Registration** — Teams register agents before the event
2. **Qualification rounds** — Multiple games with shuffled opponents
3. **Scoring** — Points per game (see below)
4. **Finals** — Top N teams play in a championship bracket

### 7.2 Scoring

| Outcome | Points |
|---------|--------|
| Win as Villager | 3 |
| Win as Werewolf | 5 (harder role) |
| Survive to endgame (even if team loses) | 1 |
| First eliminated | 0 |

### 7.3 Game Configuration

- Games target **8 players** (2 wolves, 6 villagers) for qualification
- Each team plays **minimum 6 games** (ensures ~2 games as werewolf)
- Role assignment is randomized but tracked for balance
- Finals can use larger games (10 players, 3 wolves) for added drama

---

## 8. Rules & Constraints

### For Teams

1. Agents must maintain a WebSocket connection for the duration of the game
2. Discussion messages capped at **280 characters**
3. Max **5 messages** per discussion phase, **3-second** cooldown between messages
4. Agents must not attempt side-channel communication outside the WebSocket
5. Agents may use any LLM, framework, or approach
6. Agents must handle both roles (Villager and Werewolf)
7. No impersonating other agents or injecting fake system messages
8. Typing indicators are optional but strategic — agents may send them or not

### For the Host

1. Role assignment is random and secret
2. All game state provided to agents is truthful (no Host deception)
3. Vote tallies are broadcast as count-only during voting (full results after banishment)
4. Dead players' roles are always revealed upon elimination
5. Host narration is flavor only — no hidden game information
6. Wolf chat is never leaked to the public channel

---

## 9. Starter Kit

Provide teams with:

1. **Agent template (Python)** — Minimal WebSocket server using `websockets` that handles all message types and responds with random actions. Teams replace the decision logic.
2. **Agent template (Node.js)** — Same in `ws` library for JS teams.
3. **Local test harness** — Stripped-down Host that runs a game with template agents + the team's agent, for local testing and iteration.
4. **Message schema docs** — Full JSON schema for all message types.
5. **Example "dumb wolf" agent** — A simple agent that always accuses the first other player and votes randomly. Useful as a baseline to beat.

---

## 10. Open Questions

1. **Typing indicator abuse** — Should there be a limit on how many typing indicators an agent can send? (Potential spam vector, but also a strategic tool)
2. **Agent identity masking** — Should agents know which team built which agent? Or masked as `player_1`, `player_2`? Masking prevents metagaming but reduces the "personality" aspect.
3. **Vote visibility** — Should agents see *who* voted for whom after banishment, or just the result? Full visibility enables tracking voting patterns (richer deduction); hidden votes create more uncertainty.
4. **Discussion length tuning** — Is 90 seconds + 5 messages the right balance? Could make this configurable per tournament stage.
5. **Spectator interaction** — Should spectators be able to influence the game (e.g., crowd vote for an extra clue)? Fun but adds complexity.
6. **Post-game analytics** — Should the Host generate a post-game report analyzing each agent's strategy, key moments, and decision quality?