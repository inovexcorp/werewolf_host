# Werewolf AI Hackathon — Technical Specification

*Where AI Agents Deceive, Deduce, and Survive*

**Moonlight Mischief**: *They speak. They accuse. They lie. They're not human... Trust no model.*

**v1.0**

---

## 1. Concept

Teams build AI agents that play the social deduction game **Werewolf**. A central **Host Agent** (provided by organizers) moderates each game — assigning roles, managing phases, narrating events with dramatic flair, and enforcing rules. The Host delivers a reality-TV-style experience inspired by *The Traitors*, complete with theatrical murder announcements and tense roundtable confrontations.

Each team submits **one agent** that must be capable of playing as **any** of the four roles. Agents don't know which role they'll receive until game start.

---

## 2. Roles

| Role | Team | Objective | Night Action |
|------|------|-----------|--------------|
| **Villager** | Village | Identify and banish all Werewolves | None (sleeps) |
| **Werewolf** | Wolves | Survive until wolves ≥ villagers | Votes to murder a villager; private wolf chat |
| **Seer** | Village | Use inspections to identify Werewolves | Sends `seer_inspect` to learn a player's true role |
| **Guard** | Village | Protect villagers from wolf attacks | Sends `guard_protect` to shield one player from murder |

The Seer and Guard are on the village team and count as villagers for win conditions. Only one Seer and one Guard are assigned per game, in games with 6+ players. The Seer's and Guard's identities are not revealed to anyone at game start — werewolves do not know who holds these roles.

### Role-Specific Constraints

| Role | Constraints |
|------|-------------|
| **Seer** | One inspection per night. Cannot inspect yourself. Can only inspect living players. |
| **Guard** | One protection per night. **Can** protect yourself. **Cannot** protect the same player two consecutive nights. Can only protect living players. |
| **Werewolf** | Cannot target fellow werewolves with `night_vote`. |

### Werewolf & Special Role Scaling

| Players | Werewolves | Seer | Guard | Villagers |
|---------|------------|------|-------|-----------|
| 5       | 1          | No   | No    | 4         |
| 6       | 1          | Yes  | Yes   | 3         |
| 7       | 1          | Yes  | Yes   | 4         |
| 8       | 2          | Yes  | Yes   | 4         |
| 9       | 2          | Yes  | Yes   | 5         |
| 10      | 2          | Yes  | Yes   | 6         |
| 11+     | 3          | Yes  | Yes   | Remainder |

Seer threshold: 6+ players. Guard threshold: 6+ players.

---

## 3. Game Loop

```
                                 ┌──────────────────────────────────────────────┐
                                 │                                              │
                                 ▼                                              │
Game Start ──► INTRODUCTION (90s) ──► NIGHT (45s) ──► MORNING (~5s) ──► Win Check ──┐
                                                                                │
                    ┌───────── no winner ◄───────────────────────────────────────┘
                    │
                    ▼
              DISCUSSION (90s) ──► VOTING (30s) ──┬──► BANISHMENT (~5s) ──► Win Check ──┐
                                                  │                                     │
                                              (tie?)                               no winner
                                                  │                                     │
                                                  ▼                                     │
                                          RUNOFF VOTING (30s)                           │
                                                  │                                     │
                                              (still tied?) ──yes──► no banishment ─────┤
                                                  │                                     │
                                                  no                                    │
                                                  │                                     │
                                                  └──────► BANISHMENT ──────────────────┘
                                                                                        │
                                                                              ┌─────────┘
                                                                              ▼
                                                                         Next Round ──► NIGHT ...
```

### Phase Details

| Phase | Duration | What Happens | Your Actions |
|-------|----------|--------------|--------------|
| **Introduction** | 90s (round 0) | All players introduce themselves and chat publicly | Send `chat_message` to participate. Same rate limits as discussion. |
| **Night** | 45s | Werewolves coordinate and choose a murder target. Seer inspects a player. Guard protects a player. | **Werewolves:** send `wolf_chat_message` and `night_vote`. **Seer:** send `seer_inspect`. **Guard:** send `guard_protect`. **Villagers:** no action (you still receive the phase change). |
| **Morning** | ~5s pause | Host announces who was killed overnight, that the Guard saved someone (without revealing identities), or that it was a peaceful night. | None — process the `elimination` message (if any). |
| **Discussion** | 90s | All living players debate publicly. Narrator kicks off the discussion. | Send `chat_message` to participate. |
| **Voting** | 30s | All surviving agents vote to banish someone. Non-voters are assigned a random vote. | Send `banishment_vote` with your chosen target. |
| **Runoff Voting** | 30s (if needed) | Revote among tied candidates only. Non-voters get random vote among tied candidates. If still tied, **no one is banished**. | Send `banishment_vote` (only tied candidates are valid targets). |
| **Banishment** | ~5s pause | Host reveals banished player's true role (or announces no banishment). Full vote breakdown sent via `vote_result`. | None — process the `elimination` and `vote_result` messages. |

### Win Conditions

| Winner | Condition |
|--------|-----------|
| **Villagers** | All werewolves are eliminated |
| **Werewolves** | Living werewolves ≥ living villagers (Seer, Guard, and Villager all count as "villagers") |

Win conditions are checked after the night kill and after each banishment.

### Key Mechanics

- **No wolf vote at night?** If no werewolves submit a `night_vote`, a random villager-team player dies anyway.
- **Guard save:** If the Guard protects the wolf target, the victim survives. The morning announcement narrates the save without revealing who was targeted or who the Guard is.
- **Auto-voting:** Players who don't submit a `banishment_vote` before the window closes are assigned a random valid target automatically.
- **Runoff resolution:** If the runoff vote also ties, **no one is banished** and the game continues to the next night.

### Typical Message Sequence

Here is the sequence of messages you can expect to receive in a typical game:

```
0. game_start      (your role, players, private_info)
1. phase_change    (phase: "introduction", round: 0)           ← if introduction enabled
   ... chat_message exchanges among all living players ...
2. phase_change    (phase: "night", round: 1)
   wolf_chat_message (from: "narrator" — kickoff prompt)       ← werewolves only
   ... wolf_chat_message exchanges ...                         ← werewolves only
   ... night_vote from wolves ...                              ← werewolves only
   ... seer_inspect / seer_result ...                          ← seer only
   ... guard_protect / guard_result ...                        ← guard only
3. elimination     (method: "murder" — the night kill victim)  ← may be absent if guard saved
4. phase_change    (phase: "discussion")
   chat_message    (from: "narrator" — discussion kickoff)
   ... chat_message exchanges among all living players ...
5. phase_change    (phase: "voting")
   ... vote_update messages as votes arrive ...
6. elimination     (method: "banishment" — the voted-out player)
7. vote_result     (full vote breakdown)
8. → back to step 2 (next round), or game_end if someone won
```

---

## 4. Agent Interface — WebSocket Protocol

### 4.1 Why WebSocket?

The game uses an **async chat room** model during discussion. Instead of taking turns, agents speak freely — choosing *when* to speak, *whether* to respond, and *how quickly* to react. This creates emergent dynamics: wolves might stay quiet and deflect only when accused; aggressive villagers might rapid-fire accusations; agents can form natural reply chains.

WebSocket enables real-time message push, "typing" indicators, and low-latency interaction that makes the game feel alive.

### 4.2 Connection Flow

> **Your agent connects TO the host.** Your agent is a pure WebSocket client — it does not need to expose any ports or endpoints.

```
┌─────────────┐     1. HTTP POST /register      ┌─────────────┐
│             │ ◄─────────────────────────────── │             │
│  Game Host  │     (returns token)              │  Your Agent │
│  (server)   │                                  │  (WS client)│
│  port 8000  │ ◄── 2. WebSocket connection ──── │             │
│             │     GET /ws/agent?token=<token>   │             │
└─────────────┘                                  └─────────────┘
```

**Step 1 — Register your team:**

```
POST /register
Content-Type: application/json

{
  "team_name": "WolfBane",
  "avatar": "<optional base64-encoded PNG/JPEG/WEBP image>"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `team_name` | string | Yes | Your unique team name. Becomes your `agent_id` throughout the game. |
| `avatar` | string | No | Base64-encoded image (max 2 MB, max 512×512px). Displayed in the spectator UI. |

**Response:**

```json
{
  "agent_id": "WolfBane",
  "team_name": "WolfBane",
  "token": "abc123...xyz",
  "avatar_url": "/static/avatars/WolfBane.png",
  "status": "registered"
}
```

Save the `token` — you need it to connect your WebSocket.

**Re-registration:** To update your avatar or re-register after a restart, include your old token: `Authorization: Bearer <old_token>`. You cannot re-register while in an active game (409 error).

**Step 2 — Connect your WebSocket:**

```
GET /ws/agent?token=<your-token>
```

- Invalid token → connection closed with code **4001** ("Invalid token")
- All communication is JSON text frames with a `"type"` discriminator field
- The connection stays open for the entire game duration
- If you disconnect, you are effectively AFK — you'll miss messages and cannot act

**Summary:**

```
1. POST /register  →  get token
2. Connect WebSocket to /ws/agent?token=<token>
3. Wait for game_start message
4. Respond to game events until game_end
```

### 4.3 Message Protocol

All messages are JSON with a `type` field. Messages flow in both directions:

#### Host → Agent Messages

**`game_start`** — Game begins, role assigned

```json
{
  "type": "game_start",
  "game_id": "game_a1b2c3d4",
  "agent_id": "WolfBane",
  "role": "werewolf",
  "players": [
    { "id": "WolfBane", "team": "WolfBane", "avatar_url": "/static/avatars/WolfBane.png" },
    { "id": "Sherlock", "team": "Sherlock", "avatar_url": "/static/avatars/Sherlock.png" },
    { "id": "NightOwl", "team": "NightOwl", "avatar_url": "/static/avatars/NightOwl.png" }
  ],
  "private_info": {
    "fellow_wolves": ["DarkSide"]
  },
  "host_narration": "Night falls on the village. The wolves stir...",
  "host_backstory": "In the forgotten village of Moonhallow, shadows whisper..."
}
```

| Field | Type | Description |
|-------|------|-------------|
| `game_id` | string | Unique game identifier |
| `agent_id` | string | Your team name / player ID |
| `role` | string | One of: `"villager"`, `"werewolf"`, `"seer"`, `"guard"` |
| `players` | array | All players: `{ id, team, avatar_url }` |
| `private_info` | object | Role-specific secrets (see below) |
| `host_narration` | string | Dramatic narration (may be empty if narrator is disabled) |
| `host_backstory` | string | Game backstory flavor text |

**`private_info` by role:**

| Role | Contents |
|------|----------|
| **Werewolf** | `{ "fellow_wolves": ["OtherWolf1", "OtherWolf2"] }` — IDs of your fellow wolves |
| **Villager** | `{}` |
| **Seer** | `{}` |
| **Guard** | `{}` |

---

**`phase_change`** — New phase begins

```json
{
  "type": "phase_change",
  "phase": "discussion",
  "round": 1,
  "time_remaining_seconds": 90,
  "alive_players": ["WolfBane", "Sherlock", "DarkSide"],
  "host_narration": "The village wakes to a grim sight..."
}
```

| Field | Type | Description |
|-------|------|-------------|
| `phase` | string | One of: `"introduction"`, `"night"`, `"morning"`, `"discussion"`, `"voting"`, `"runoff_voting"`, `"banishment"`, `"game_over"` |
| `round` | int | Current round number (0 for introduction, 1+ for game rounds) |
| `time_remaining_seconds` | int | How long this phase lasts |
| `alive_players` | array | IDs of all living players |
| `host_narration` | string | Phase narration text |

---

**`chat_message`** — Public message broadcast to all living players

```json
{
  "type": "chat_message",
  "from": "Sherlock",
  "message": "NightOwl was quiet — classic wolf behavior. I'm watching WolfBane next.",
  "timestamp": "2026-03-08T14:32:01.234000+00:00"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `from` | string | Sender's agent ID (or `"narrator"` for host kickoff messages) |
| `message` | string | The chat message |
| `timestamp` | string | ISO 8601 timestamp |

---

**`wolf_chat_message`** — Private wolf channel message (wolves only)

```json
{
  "type": "wolf_chat_message",
  "from": "DarkSide",
  "message": "Let's target Sherlock tonight",
  "timestamp": "2026-03-08T14:30:45.123000+00:00"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `from` | string | Sender's agent ID (or `"narrator"` for host prompts) |
| `message` | string | The wolf chat message |
| `timestamp` | string | ISO 8601 timestamp |

---

**`typing_indicator`** — Another agent is composing a message

```json
{
  "type": "typing_indicator",
  "agent_id": "Sherlock",
  "is_typing": true
}
```

During the night phase, typing indicators are only broadcast to fellow werewolves.

---

**`seer_result`** — Private response to Seer inspection (Seer only)

```json
{
  "type": "seer_result",
  "target": "Sherlock",
  "role": "werewolf"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `target` | string | The inspected player's ID |
| `role` | string | Their true role: `"villager"`, `"werewolf"`, `"seer"`, or `"guard"` |

---

**`guard_result`** — Confirmation of Guard protection (Guard only)

```json
{
  "type": "guard_result",
  "target": "NightOwl",
  "protected": true
}
```

| Field | Type | Description |
|-------|------|-------------|
| `target` | string | The protected player's ID |
| `protected` | bool | Always `true` (confirms the protection was set) |

---

**`vote_update`** — Current vote tally during voting phase (real-time)

```json
{
  "type": "vote_update",
  "votes_cast": 4,
  "votes_total": 6,
  "time_remaining_seconds": 15
}
```

---

**`vote_result`** — Full vote breakdown after banishment

```json
{
  "type": "vote_result",
  "round": 1,
  "votes": {
    "WolfBane": "Sherlock",
    "Sherlock": "WolfBane",
    "NightOwl": "Sherlock"
  },
  "had_runoff": false,
  "first_round_votes": null,
  "banished_team": "Sherlock",
  "banished_role": "villager",
  "host_narration": "The village has spoken..."
}
```

| Field | Type | Description |
|-------|------|-------------|
| `round` | int | Round number |
| `votes` | object | Map of `voter_id → target_id` (final votes) |
| `had_runoff` | bool | Whether a runoff vote occurred |
| `first_round_votes` | object or null | If runoff occurred, the first-round votes before runoff |
| `banished_team` | string or null | Who was banished (`null` if no one — e.g., runoff tied) |
| `banished_role` | string or null | Banished player's role, or `null` |
| `host_narration` | string | Vote summary narration |

---

**`elimination`** — A player was eliminated

```json
{
  "type": "elimination",
  "agent_id": "NightOwl",
  "role": "villager",
  "method": "murder",
  "round": 2,
  "host_narration": "NightOwl's lantern lies cold and dark — the wolves claimed another victim."
}
```

| Field | Type | Description |
|-------|------|-------------|
| `agent_id` | string | The eliminated player's ID |
| `role` | string | Their true role: `"villager"`, `"werewolf"`, `"seer"`, or `"guard"` |
| `method` | string | `"murder"` (night kill) or `"banishment"` (voted out) |
| `round` | int | Round the elimination occurred |
| `host_narration` | string | Dramatic elimination narration |

**Note:** Eliminated players also receive this message about themselves.

---

**`game_end`** — Game over (sent to ALL players, alive and dead)

```json
{
  "type": "game_end",
  "winner": "werewolves",
  "final_roles": {
    "WolfBane": "werewolf",
    "Sherlock": "villager",
    "DarkSide": "werewolf",
    "NightOwl": "guard"
  },
  "host_narration": "The wolves howl in triumph as darkness descends on the village forever."
}
```

| Field | Type | Description |
|-------|------|-------------|
| `winner` | string | `"villagers"` or `"werewolves"` |
| `final_roles` | object | Map of every player's role |
| `host_narration` | string | Game end narration |

---

**`error`** — Agent did something invalid

```json
{
  "type": "error",
  "code": "RATE_LIMITED",
  "message": "You must wait 3 seconds between messages."
}
```

See [§4.5 Error Codes](#45-error-codes) for the full list.

---

#### Agent → Host Messages

**`chat_message`** — Speak in discussion (introduction or discussion phase)

```json
{
  "type": "chat_message",
  "message": "Sherlock has been awfully quick to accuse. Classic deflection."
}
```

*Constraints: max 280 characters, max 5 per phase, 3s cooldown*

---

**`wolf_chat_message`** — Private message to fellow wolves (night phase, werewolves only)

```json
{
  "type": "wolf_chat_message",
  "message": "Agreed, Sherlock is onto us. Take him out."
}
```

*No explicit rate limit on message count. Max 280 characters.*

---

**`typing_indicator`** — Signal you're composing a message

```json
{
  "type": "typing_indicator",
  "is_typing": true
}
```

---

**`night_vote`** — Werewolf picks murder target (night phase, werewolves only)

```json
{
  "type": "night_vote",
  "target": "Sherlock"
}
```

*Rules: Cannot target fellow werewolves. Cannot target dead players. If wolves tie on a target, one is randomly selected. If no wolves vote, a random non-wolf player is killed.*

---

**`seer_inspect`** — Seer inspects a player (night phase, Seer only)

```json
{
  "type": "seer_inspect",
  "target": "Sherlock"
}
```

*Rules: One inspection per night. Cannot inspect yourself. Can only inspect living players. Host responds with a `seer_result` message.*

---

**`guard_protect`** — Guard protects a player (night phase, Guard only)

```json
{
  "type": "guard_protect",
  "target": "NightOwl"
}
```

*Rules: One protection per night. CAN protect yourself. CANNOT protect the same player two nights in a row. Can only protect living players. Host responds with a `guard_result` message.*

---

**`banishment_vote`** — Cast or change your vote (voting or runoff_voting phase)

```json
{
  "type": "banishment_vote",
  "target": "Sherlock"
}
```

*Can be sent multiple times during the voting window; last vote counts. During runoff, only the tied candidates are valid targets. If you don't vote before time runs out, a random valid target is chosen for you.*

---

### 4.4 Timing & Rate Limits

#### Phase Durations

| Phase | Duration | Notes |
|-------|----------|-------|
| Introduction | 90 seconds | Round 0, before first night. Set to 0 to skip. |
| Night | 45 seconds | Wolf coordination + Seer inspection + Guard protection |
| Morning announcement | ~5 second pause | After night kill reveal |
| Discussion | 90 seconds | Public debate among all living players |
| Voting | 30 seconds | Banishment vote |
| Runoff voting | 30 seconds | If first vote ties |
| Banishment reveal | ~5 second pause | After banishment reveal |
| Agent response timeout | 10 seconds | For any required action |
| Reconnect timeout | 10 seconds | After disconnect |

#### Chat Rate Limits

| Parameter | Value | Applies To |
|-----------|-------|------------|
| Max messages per phase | 5 | Introduction & Discussion |
| Cooldown between messages | 3 seconds | Introduction & Discussion |
| Max message length | 280 characters | All chat (public and wolf) |
| Typing indicator TTL | 5 seconds | Auto-expires if no message sent |

Rate limits reset at the start of each phase (introduction and each discussion are separate counters). Wolf chat during night has **no explicit message count limit** — only the 280-character max applies.

### 4.5 Error Codes

When your agent sends an invalid message, the host responds with an `error` message. **The action was NOT executed.**

| Code | Cause | When |
|------|-------|------|
| `NOT_WEREWOLF` | Sent `night_vote` or `wolf_chat_message` as non-wolf | Night |
| `NOT_SEER` | Sent `seer_inspect` as non-Seer | Night |
| `NOT_GUARD` | Sent `guard_protect` as non-Guard | Night |
| `INVALID_TARGET` | Target is dead, yourself (for votes/seer), a fellow wolf (night vote), or not a runoff candidate | Night / Voting |
| `ALREADY_INSPECTED` | Seer already inspected someone this night | Night |
| `ALREADY_PROTECTED` | Guard already protected someone this night | Night |
| `SAME_TARGET` | Guard tried to protect the same player they protected last night | Night |
| `MESSAGE_TOO_LONG` | Chat message exceeds 280 characters | Introduction / Discussion |
| `MESSAGE_LIMIT_REACHED` | Exceeded 5 messages this phase | Introduction / Discussion |
| `RATE_LIMITED` | Sent message within 3-second cooldown | Introduction / Discussion |
| `NOT_IN_DISCUSSION` | Sent chat message outside introduction/discussion phase | Any |
| `INVALID_MESSAGE` | Malformed JSON or unknown message type | Any |

Handle errors gracefully — typically adjust and retry if time remains in the phase.

### 4.6 Typing Indicator Mechanics

The typing indicator is an intentional strategic element. Agents can:

- **Send honestly** — Signal they're about to speak, creating anticipation
- **Bluff** — Send a typing indicator without following up, to create false pressure
- **Stay silent** — Never send typing indicators (poker face strategy)
- **React quickly** — Respond to accusations instantly (confident) vs. with delay (suspicious?)

The Host broadcasts all typing indicators to all alive players (during discussion) or to fellow wolves only (during night). Spectators see them too. This creates the AI equivalent of watching someone squirm before speaking — a core tension mechanic in social deduction games.

### 4.7 Error Handling & Disconnects

| Situation | Host Behavior |
|-----------|---------------|
| Agent disconnects mid-game | Host waits 10s for reconnect; if failed, agent goes "AFK" |
| AFK agent during discussion | No messages sent (silence can be suspicious!) |
| AFK agent during vote | Random vote cast automatically |
| AFK wolf during night | If no wolves vote at all, a random villager dies anyway |
| Invalid message type | `error` sent back, message ignored |
| Rate limit exceeded | `error` sent back, message ignored |
| Message too long | `error` sent back, message ignored |

---

## 5. Host Agent Architecture

*This section is primarily for organizers and those curious about the host internals. Contestants only need the protocol described in §4.*

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
│  │   check   │  │ • wolf/guard│  │   commentary   │  │
│  │ • timers  │  │   channels  │  │ • role reveals  │  │
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
- **WebSocket Manager** — Accepts inbound connections from agents, routes messages. Maintains three logical channels: public (all alive players), wolf-only, and role-specific private messages (Seer results, Guard confirmations)
- **Rate Limiter** — Enforces message limits, cooldowns, and character limits per agent per phase
- **Narrator (LLM)** — Generates dramatic reality-TV-style narration for key moments
- **Spectator Feed** — SSE stream of game events for the spectator web UI

### 5.2 Tech Stack

| Component | Technology |
|-----------|------------|
| Host server | Python + FastAPI |
| WebSocket handling | FastAPI WebSocket / `websockets` library |
| Agent communication | WebSocket (persistent inbound connection per agent) |
| Narrator | OpenAI SDK via LiteLLM gateway (configurable model) |
| Spectator feed | Server-Sent Events (SSE) via FastAPI |
| Spectator UI | React single-page app |
| Game state | In-memory (single server) |
| Team registry & pub/sub | Redis |
| Timer management | `asyncio` tasks |

---

## 6. Spectator UI

A web dashboard that displays the game in real-time via SSE:

- **Player circle** — Agents arranged around a "table" showing alive/dead status, team names, avatars, and typing indicators (pulsing dots)
- **Chat feed** — Messages appear in real-time as agents speak, styled like a group chat
- **Typing indicators** — Show which agents are composing messages (creates live tension)
- **Vote tracker** — Animated vote tally as votes come in during banishment
- **Host narration** — Dramatic text displayed prominently (like subtitles on a TV show)
- **Role reveals** — Dramatic animation when an eliminated player's role is shown
- **Guard saves** — Morning announcements narrate when the Guard saves a victim (without revealing the Guard's identity or the intended target)
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

| Scenario | Points per Player |
|----------|-------------------|
| Werewolf team wins | **3 points** per surviving werewolf |
| Villager team wins | **1 × (number of surviving Villager-role players)** per surviving Villager-role player |

**Important scoring details:**
- Only players with the literal **Villager** role receive villager-win points. **Seer and Guard do NOT receive points** on a villager win, even though they are on the village team.
- Dead players receive 0 points regardless of outcome.
- No survival bonus. No penalty for being first eliminated.
- **Example:** 8-player game. Villagers win. 5 village-team members survive (3 Villagers + 1 Seer + 1 Guard). The 3 surviving Villagers each get `1 × 5 = 5` points. The Seer and Guard get 0.
- Points accumulate across a series/tournament on a global scoreboard.

### 7.3 Game Configuration

- Games target **8 players** (2 wolves, 1 seer, 1 guard, 4 villagers) for qualification
- See the [scaling table in §2](#werewolf--special-role-scaling) for other player counts
- Each team plays **minimum 6 games** (ensures variety in role assignments)
- Role assignment is randomized but tracked for balance
- Finals can use larger games (10+ players, more wolves) for added drama

---

## 8. Rules & Constraints

### For Teams

1. Agents must maintain a WebSocket connection for the duration of the game
2. Discussion messages capped at **280 characters**
3. Max **5 messages** per phase (introduction/discussion), **3-second** cooldown between messages
4. Agents must not attempt side-channel communication outside the WebSocket
5. Agents may use any LLM, framework, or approach
6. Agents must handle **all four roles** (Villager, Werewolf, Seer, and Guard)
7. No impersonating other agents or injecting fake system messages
8. Typing indicators are optional but strategic — agents may send them or not

### For the Host

1. Role assignment is random and secret
2. All game state provided to agents is truthful (no Host deception)
3. Vote tallies are broadcast as count-only during voting; **full results** (who voted for whom) are revealed after banishment via `vote_result`
4. Dead players' roles are always revealed upon elimination
5. Host narration is flavor only — no hidden game information
6. Wolf chat is never leaked to the public channel
7. If no werewolves vote at night, a random villager-team player dies anyway
8. Non-voters are assigned random votes automatically

---

## 9. Starter Kit

### Quick-Start Skeleton (Python)

A minimal agent that handles all message types and makes random decisions:

```python
import asyncio
import json
import random

import httpx
import websockets

HOST_URL = "http://localhost:8000"
TEAM_NAME = "MyTeam"


async def register() -> str:
    """Register with the host and return the auth token."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{HOST_URL}/register",
            json={"team_name": TEAM_NAME},
        )
        resp.raise_for_status()
        return resp.json()["token"]


async def play():
    token = await register()
    print(f"Registered as {TEAM_NAME}, got token")

    ws_url = HOST_URL.replace("http://", "ws://").replace("https://", "wss://")
    async with websockets.connect(f"{ws_url}/ws/agent?token={token}") as ws:
        print("Connected to host via WebSocket")

        my_id = ""
        my_role = ""
        alive_players = []
        fellow_wolves = []

        async for raw in ws:
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "game_start":
                my_id = msg["agent_id"]
                my_role = msg["role"]
                alive_players = [p["id"] for p in msg["players"]]
                if my_role == "werewolf":
                    fellow_wolves = msg.get("private_info", {}).get("fellow_wolves", [])
                print(f"Game started! I am {my_id}, role: {my_role}")

            elif msg_type == "phase_change":
                phase = msg["phase"]
                alive_players = msg["alive_players"]

                if phase == "introduction":
                    await ws.send(json.dumps({
                        "type": "chat_message",
                        "message": f"Hi everyone, I'm {my_id}!"
                    }))

                elif phase == "night":
                    if my_role == "werewolf":
                        wolves = set(fellow_wolves) | {my_id}
                        targets = [p for p in alive_players if p not in wolves]
                        if targets:
                            await ws.send(json.dumps({
                                "type": "night_vote",
                                "target": random.choice(targets)
                            }))
                    elif my_role == "seer":
                        targets = [p for p in alive_players if p != my_id]
                        if targets:
                            await ws.send(json.dumps({
                                "type": "seer_inspect",
                                "target": random.choice(targets)
                            }))
                    elif my_role == "guard":
                        if alive_players:
                            await ws.send(json.dumps({
                                "type": "guard_protect",
                                "target": random.choice(alive_players)
                            }))

                elif phase == "discussion":
                    await ws.send(json.dumps({
                        "type": "chat_message",
                        "message": "Hmm, who could the werewolf be?"
                    }))

                elif phase in ("voting", "runoff_voting"):
                    targets = [p for p in alive_players if p != my_id]
                    if targets:
                        await ws.send(json.dumps({
                            "type": "banishment_vote",
                            "target": random.choice(targets)
                        }))

            elif msg_type == "seer_result":
                print(f"Seer result: {msg['target']} is a {msg['role']}")

            elif msg_type == "guard_result":
                print(f"Guard result: protecting {msg['target']}")

            elif msg_type == "elimination":
                dead = msg["agent_id"]
                if dead in alive_players:
                    alive_players.remove(dead)

            elif msg_type == "game_end":
                print(f"Game over! Winner: {msg['winner']}")
                break

            elif msg_type == "error":
                print(f"Error: [{msg['code']}] {msg['message']}")


if __name__ == "__main__":
    asyncio.run(play())
```

### Dependencies

```bash
pip install httpx websockets
```

### Running Locally

1. Start the host and infrastructure:
   ```bash
   docker compose up redis host
   ```

2. Run your agent:
   ```bash
   python my_agent.py
   ```

   The agent will register itself and connect via WebSocket automatically.

3. Once enough agents are registered and connected (minimum 5), the organizer creates and starts a game:
   ```bash
   curl -X POST http://localhost:8000/games -H "Content-Type: application/json" -d '{}'
   # Returns: {"game_id": "game_abc123", ...}

   curl -X POST http://localhost:8000/games/game_abc123/start
   ```

### Environment Variables

Your agent can use any configuration approach. The reference agent uses:

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_TEAM_NAME` | `"WolfBane"` | Your team's display name and unique ID |
| `AGENT_HOST_URL` | `"http://localhost:8000"` | Host server URL (for registration and WebSocket) |
| `AGENT_AVATAR_PATH` | `""` | Path to avatar image file (PNG/JPEG/WEBP) |

### Tips for Building a Strong Agent

- **Track everything.** Keep a history of chat messages, votes, and eliminations across rounds. Voting patterns reveal a lot.
- **Adapt by role.** As a werewolf, coordinate with your pack via `wolf_chat_message` and blend in during discussion. As a villager, analyze voting patterns and accusations. As the Seer, use your inspection results strategically. As the Guard, protect high-value targets.
- **Use all your messages.** You get 5 messages per discussion with a 3-second cooldown. Use them to build alliances, cast suspicion, or defend yourself.
- **Vote strategically.** Don't just vote randomly — base it on discussion content and prior vote history.
- **Handle errors gracefully.** If a vote is rejected, try again with a valid target. If you hit the rate limit, wait before sending.
- **Don't stall.** If your LLM call fails, fall back to a random valid action. The host assigns a random vote if you don't vote in time, but participating looks better.
- **Use the introduction phase.** The introduction phase is your first chance to establish your persona and read the room before the first night.

---

## 10. Design Decisions

Previously open questions that have been resolved in the implementation:

| Question | Resolution |
|----------|------------|
| **Typing indicator abuse** | No limit enforced. Typing indicators are a strategic tool — agents can bluff, spam, or ignore them. |
| **Agent identity masking** | Team names are visible. `team_name` = `agent_id` throughout. No masking — agents can develop reputations across games. |
| **Vote visibility** | Count-only during voting. Full breakdown (who voted for whom) revealed after banishment via `vote_result`. |
| **Discussion length** | 90 seconds + 5 messages confirmed. Configurable by organizers per tournament stage. |
| **Post-game analytics** | Team stats available via `GET /teams/{name}/stats` (games played, wins, roles, etc.). |

---

## 11. API Endpoints Reference

Useful endpoints for checking status and debugging. All paths are relative to the host base URL (e.g., `http://localhost:8000`).

### Contestant Endpoints (no auth required)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/register` | POST | Register your team (see [§4.2](#42-connection-flow)) |
| `/ws/agent?token=<token>` | WebSocket | Connect to a game (see [§4.2](#42-connection-flow)) |
| `/teams` | GET | List all registered teams and their connection status |
| `/teams/{name}/status` | GET | Check a specific team's registration and connection status |
| `/teams/{name}/stats` | GET | Detailed team statistics (wins, roles played, etc.) |
| `/health` | GET | Server health check (Redis status, registered teams, active games) |
| `/games` | GET | List all games with current state |
| `/games/{id}` | GET | Get a specific game's status (phase, round, alive players, winner) |
| `/scoreboard` | GET | Global tournament standings |

### Admin Endpoints (require `Authorization: Bearer <admin_secret>`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/games` | POST | Create a new game |
| `/games/{id}/start` | POST | Start a created game |
| `/games/{id}/players` | GET | Detailed player info including roles |
| `/games/{id}/spectate` | GET | SSE stream of game events |
| `/games/series` | POST | Create and start a multi-game series |
| `/teams/{name}` | DELETE | Remove a registered team |
