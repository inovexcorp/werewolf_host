import asyncio
import json
import logging
import random
from collections import Counter

from app.config import settings, wolves_for_player_count
from app.models.game import (
    Elimination,
    EliminationMethod,
    GameState,
    Phase,
    Player,
    Role,
)
from app.models.messages import (
    AgentBanishmentVote,
    AgentChatMessage,
    AgentNightVote,
    AgentTypingIndicator,
    AgentWolfChat,
    EliminationMessage,
    ErrorMessage,
    GameEndMessage,
    GameStartMessage,
    PhaseChangeMessage,
    VoteResultMessage,
    VoteUpdateMessage,
)
from app.narrator import Narrator
from app.rate_limiter import rate_limiter
from app.redis import publish_event
from app.scoring import award_points
from app.ws_manager import ConnectionManager

logger = logging.getLogger(__name__)

NARRATOR_ID = "narrator"


class GameEngine:
    def __init__(
        self,
        game_id: str,
        players: list[Player],
        narrator: Narrator,
        host_backstory: str = "",
    ):
        self.state = GameState(game_id=game_id)
        self.ws = ConnectionManager()
        self.narrator = narrator
        self.host_backstory = host_backstory
        self._phase_task: asyncio.Task | None = None
        self._had_runoff: bool = False
        self._first_round_votes: dict[str, str] = {}
        self._background_tasks: set[asyncio.Task] = set()

        for p in players:
            self.state.players[p.id] = p

    def _fire_and_forget(self, coro) -> None:
        """Create a background task and prevent it from being garbage collected."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def setup(self) -> list[str]:
        """Connect to all agents. Returns list of agent_ids that failed."""
        failures = []
        for p in self.state.players.values():
            ok = await self.ws.connect(p.id, p.ws_url)
            if not ok:
                failures.append(p.id)
        return failures

    async def run(self):
        """Main game loop. Call after setup()."""
        try:
            self._assign_roles()
            await self._send_game_start()

            while not self.state.winner:
                self.state.round += 1

                await self._night_phase()
                victim = self._resolve_night_votes()
                await self._morning_announcement(victim)

                if self._check_win():
                    break

                await self._discussion_phase()
                banished = await self._voting_phase()
                await self._banishment_reveal(banished)

                self._check_win()

            await self._send_game_end()
            await award_points(self.state)
        finally:
            await self.ws.disconnect_all()

    # ------------------------------------------------------------------
    # Role assignment
    # ------------------------------------------------------------------

    def _assign_roles(self):
        player_list = list(self.state.players.values())
        n_wolves = wolves_for_player_count(len(player_list))
        random.shuffle(player_list)

        for i, p in enumerate(player_list):
            p.role = Role.WEREWOLF if i < n_wolves else Role.VILLAGER

    # ------------------------------------------------------------------
    # Game start
    # ------------------------------------------------------------------

    async def _send_game_start(self):
        narration = await self.narrator.narrate_game_start(
            [p.info for p in self.state.players.values()]
        )
        all_players_info = [p.info for p in self.state.players.values()]
        wolf_ids = [
            p.id for p in self.state.players.values() if p.role == Role.WEREWOLF
        ]

        for p in self.state.players.values():
            private_info = {}
            if p.role == Role.WEREWOLF:
                private_info["fellow_wolves"] = [w for w in wolf_ids if w != p.id]

            msg = GameStartMessage(
                game_id=self.state.game_id,
                agent_id=p.id,
                role=p.role,
                players=all_players_info,
                private_info=private_info,
                host_narration=narration,
                host_backstory=self.host_backstory,
            )
            await self.ws.send(p.id, msg)

        for p in self.state.players.values():
            self.ws.start_listening(p.id)

        self.narrator.summary.record_game_start(
            [p.id for p in self.state.players.values()]
        )

        await self._publish(
            "game_start",
            {
                "narration": narration,
                "players": [
                    {"id": p.id, "team": p.team, "avatar_url": p.avatar_url}
                    for p in self.state.players.values()
                ],
            },
        )

    # ------------------------------------------------------------------
    # Night phase
    # ------------------------------------------------------------------

    async def _night_phase(self):
        self.state.phase = Phase.NIGHT
        self.state.night_votes = {}

        narration = await self.narrator.narrate_phase("night", self.state.round)

        phase_msg = PhaseChangeMessage(
            phase=Phase.NIGHT,
            round=self.state.round,
            time_remaining_seconds=settings.night_duration,
            alive_players=self.state.alive_player_ids,
            host_narration=narration,
        )
        await self.ws.broadcast(self.state.alive_player_ids, phase_msg)
        await self._publish(
            "phase_change", {"phase": "night", "round": self.state.round}
        )

        # Narrator kickoff message to wolf chat
        wolf_ids = [p.id for p in self.state.alive_wolves]
        kickoff = await self.narrator.generate_wolf_kickoff(self.state.round)
        await self.ws.broadcast_wolf_chat(wolf_ids, NARRATOR_ID, kickoff)
        await self._publish(
            "wolf_chat_message",
            {
                "from": NARRATOR_ID,
                "message": kickoff,
                "round": self.state.round,
            },
        )
        self.state.chat_log.append(
            {
                "channel": "wolf",
                "from": NARRATOR_ID,
                "message": kickoff,
                "round": self.state.round,
                "phase": "night",
            }
        )

        await self._collect_messages_for(
            settings.night_duration,
            allowed_handler=self._handle_night_message,
        )

    def _handle_night_message(self, agent_id: str, msg) -> bool:
        player = self.state.players.get(agent_id)
        if not player or not player.alive:
            return False

        if isinstance(msg, AgentNightVote):
            if player.role != Role.WEREWOLF:
                self._fire_and_forget(
                    self.ws.send(
                        agent_id,
                        ErrorMessage(
                            code="NOT_WEREWOLF",
                            message="Only werewolves can vote at night.",
                        ),
                    )
                )
                return False
            if msg.target not in self.state.alive_player_ids or msg.target == agent_id:
                self._fire_and_forget(
                    self.ws.send(
                        agent_id,
                        ErrorMessage(code="INVALID_TARGET", message="Invalid target."),
                    )
                )
                return False
            # Wolves shouldn't target fellow wolves
            target = self.state.players.get(msg.target)
            if target and target.role == Role.WEREWOLF:
                self._fire_and_forget(
                    self.ws.send(
                        agent_id,
                        ErrorMessage(
                            code="INVALID_TARGET",
                            message="Cannot target a fellow werewolf.",
                        ),
                    )
                )
                return False
            self.state.night_votes[agent_id] = msg.target
            return True

        if isinstance(msg, AgentWolfChat):
            if player.role != Role.WEREWOLF:
                self._fire_and_forget(
                    self.ws.send(
                        agent_id,
                        ErrorMessage(
                            code="NOT_WEREWOLF",
                            message="Only werewolves can use wolf chat.",
                        ),
                    )
                )
                return False
            wolf_ids = [p.id for p in self.state.alive_wolves]
            self._fire_and_forget(
                self.ws.broadcast_wolf_chat(wolf_ids, agent_id, msg.message)
            )
            self._fire_and_forget(
                self._publish(
                    "wolf_chat_message",
                    {
                        "from": agent_id,
                        "message": msg.message,
                        "round": self.state.round,
                    },
                )
            )
            self.state.chat_log.append(
                {
                    "channel": "wolf",
                    "from": agent_id,
                    "message": msg.message,
                    "round": self.state.round,
                    "phase": "night",
                }
            )
            return True

        if isinstance(msg, AgentTypingIndicator):
            if player.role == Role.WEREWOLF:
                wolf_ids = [p.id for p in self.state.alive_wolves if p.id != agent_id]
                self._fire_and_forget(
                    self.ws.broadcast_typing(wolf_ids, agent_id, msg.is_typing)
                )
            return True

        return False

    def _resolve_night_votes(self) -> Player | None:
        if not self.state.night_votes:
            # Wolves didn't vote — random villager dies
            targets = self.state.alive_villagers
            if not targets:
                return None
            return random.choice(targets)

        vote_counts = Counter(self.state.night_votes.values())
        max_votes = max(vote_counts.values())
        top_targets = [t for t, c in vote_counts.items() if c == max_votes]
        target_id = random.choice(top_targets)
        return self.state.players.get(target_id)

    # ------------------------------------------------------------------
    # Morning announcement
    # ------------------------------------------------------------------

    async def _morning_announcement(self, victim: Player | None):
        self.state.phase = Phase.MORNING

        if victim and victim.alive:
            victim.alive = False
            elim = Elimination(
                agent_id=victim.id,
                role=victim.role,
                method=EliminationMethod.MURDER,
                round=self.state.round,
            )
            self.state.eliminations.append(elim)

            narration = await self.narrator.narrate_murder(victim.id, victim.role)
            elim_msg = EliminationMessage(
                agent_id=victim.id,
                role=victim.role,
                method=EliminationMethod.MURDER,
                round=self.state.round,
                host_narration=narration,
            )
            await self.ws.broadcast(self.state.alive_player_ids, elim_msg)
            # Also send to the victim so they know
            await self.ws.send(victim.id, elim_msg)
            await self._publish(
                "elimination",
                {
                    "agent_id": victim.id,
                    "role": victim.role,
                    "method": "murder",
                    "narration": narration,
                },
            )
            self.narrator.summary.record_night_result(self.state.round, victim.id)
        else:
            self.narrator.summary.record_night_result(self.state.round, None)

        await asyncio.sleep(settings.morning_announcement_pause)

    # ------------------------------------------------------------------
    # Discussion phase
    # ------------------------------------------------------------------

    async def _discussion_phase(self):
        self.state.phase = Phase.DISCUSSION

        rate_limiter.reset_for_phase(self.state.game_id, self.state.alive_player_ids)

        narration = await self.narrator.narrate_phase("discussion", self.state.round)

        phase_msg = PhaseChangeMessage(
            phase=Phase.DISCUSSION,
            round=self.state.round,
            time_remaining_seconds=settings.discussion_duration,
            alive_players=self.state.alive_player_ids,
            host_narration=narration,
        )
        await self.ws.broadcast(self.state.alive_player_ids, phase_msg)
        await self._publish(
            "phase_change", {"phase": "discussion", "round": self.state.round}
        )

        # Narrator kickoff message to public chat
        kickoff = await self.narrator.generate_discussion_kickoff(self.state.round)
        await self.ws.broadcast_chat(self.state.alive_player_ids, NARRATOR_ID, kickoff)
        await self._publish(
            "chat_message",
            {
                "from": NARRATOR_ID,
                "message": kickoff,
            },
        )
        self.state.chat_log.append(
            {
                "channel": "public",
                "from": NARRATOR_ID,
                "message": kickoff,
                "round": self.state.round,
                "phase": "discussion",
            }
        )

        await self._collect_messages_for(
            settings.discussion_duration,
            allowed_handler=self._handle_discussion_message,
        )

        # Build discussion highlights for narrator context (public messages only)
        round_chats = [
            {
                "team": self.state.players[entry["from"]].id,
                "message": entry["message"],
            }
            for entry in self.state.chat_log
            if entry.get("round") == self.state.round
            and entry.get("phase") == "discussion"
            and entry.get("channel") == "public"
            and entry.get("from") != NARRATOR_ID
        ]
        self.narrator.summary.record_discussion_highlights(
            self.state.round, round_chats
        )

    def _handle_discussion_message(self, agent_id: str, msg) -> bool:
        player = self.state.players.get(agent_id)
        if not player or not player.alive:
            return False

        if isinstance(msg, AgentChatMessage):
            error_code = rate_limiter.check_chat_message(
                self.state.game_id, agent_id, msg.message
            )
            if error_code:
                self._fire_and_forget(
                    self.ws.send(
                        agent_id,
                        ErrorMessage(
                            code=error_code, message=f"Chat rejected: {error_code}"
                        ),
                    )
                )
                return False

            self._fire_and_forget(
                self.ws.broadcast_chat(
                    self.state.alive_player_ids, agent_id, msg.message
                )
            )
            self.state.chat_log.append(
                {
                    "channel": "public",
                    "from": agent_id,
                    "message": msg.message,
                    "round": self.state.round,
                    "phase": "discussion",
                }
            )
            self._fire_and_forget(
                self._publish(
                    "chat_message",
                    {
                        "from": agent_id,
                        "message": msg.message,
                    },
                )
            )
            return True

        if isinstance(msg, AgentTypingIndicator):
            self._fire_and_forget(
                self.ws.broadcast_typing(
                    [p for p in self.state.alive_player_ids if p != agent_id],
                    agent_id,
                    msg.is_typing,
                )
            )
            self._fire_and_forget(
                self._publish(
                    "typing_indicator",
                    {
                        "agent_id": agent_id,
                        "is_typing": msg.is_typing,
                    },
                )
            )
            return True

        return False

    # ------------------------------------------------------------------
    # Voting phase
    # ------------------------------------------------------------------

    async def _voting_phase(self) -> Player | None:
        self._had_runoff = False
        self._first_round_votes = {}
        return await self._run_vote_round(settings.voting_duration, is_runoff=False)

    async def _run_vote_round(
        self, duration: int, is_runoff: bool, candidates: list[str] | None = None
    ) -> Player | None:
        if is_runoff:
            self._first_round_votes = dict(self.state.banishment_votes)
        phase = Phase.RUNOFF_VOTING if is_runoff else Phase.VOTING
        self.state.phase = phase
        self.state.banishment_votes = {}

        phase_msg = PhaseChangeMessage(
            phase=phase,
            round=self.state.round,
            time_remaining_seconds=duration,
            alive_players=self.state.alive_player_ids,
            host_narration="The village must vote."
            if not is_runoff
            else "A runoff vote begins!",
        )
        await self.ws.broadcast(self.state.alive_player_ids, phase_msg)
        await self._publish(
            "phase_change", {"phase": str(phase), "round": self.state.round}
        )

        total_voters = len(self.state.alive_player_ids)

        async def vote_handler(agent_id: str, msg) -> bool:
            player = self.state.players.get(agent_id)
            if not player or not player.alive:
                return False

            if isinstance(msg, AgentBanishmentVote):
                target = msg.target
                valid_targets = candidates or [
                    p for p in self.state.alive_player_ids if p != agent_id
                ]
                if target not in valid_targets:
                    self._fire_and_forget(
                        self.ws.send(
                            agent_id,
                            ErrorMessage(
                                code="INVALID_TARGET", message="Invalid vote target."
                            ),
                        )
                    )
                    return False
                self.state.banishment_votes[agent_id] = target

                vote_update = VoteUpdateMessage(
                    votes_cast=len(self.state.banishment_votes),
                    votes_total=total_voters,
                    time_remaining_seconds=0,  # approximate
                )
                self._fire_and_forget(
                    self.ws.broadcast(self.state.alive_player_ids, vote_update)
                )
                return True

            return False

        await self._collect_messages_for(duration, allowed_handler=vote_handler)

        # Assign random votes for agents who didn't vote
        for pid in self.state.alive_player_ids:
            if pid not in self.state.banishment_votes:
                valid = candidates or [
                    p for p in self.state.alive_player_ids if p != pid
                ]
                if valid:
                    self.state.banishment_votes[pid] = random.choice(valid)

        return await self._resolve_banishment_votes(is_runoff, candidates)

    async def _resolve_banishment_votes(
        self, is_runoff: bool, candidates: list[str] | None
    ) -> Player | None:
        if not self.state.banishment_votes:
            return None

        vote_counts = Counter(self.state.banishment_votes.values())
        max_votes = max(vote_counts.values())
        tied = [t for t, c in vote_counts.items() if c == max_votes]

        if len(tied) == 1:
            return self.state.players.get(tied[0])

        if is_runoff:
            # Second tie → random elimination among tied
            return self.state.players.get(random.choice(tied))

        # First tie → runoff
        self._had_runoff = True
        return await self._run_vote_round(
            settings.runoff_voting_duration, is_runoff=True, candidates=tied
        )

    # ------------------------------------------------------------------
    # Vote summary helpers
    # ------------------------------------------------------------------

    def _player_roster(self) -> list[dict]:
        """Build a roster of all players with status and role info."""
        return [
            {
                "id": p.id,
                "team": p.team,
                "role": p.role.value if p.role else "unknown",
                "alive": p.alive,
                "avatar_url": p.avatar_url,
            }
            for p in self.state.players.values()
        ]

    # ------------------------------------------------------------------
    # Banishment reveal
    # ------------------------------------------------------------------

    async def _banishment_reveal(self, banished: Player | None):
        self.state.phase = Phase.BANISHMENT

        if banished and banished.alive:
            banished.alive = False
            elim = Elimination(
                agent_id=banished.id,
                role=banished.role,
                method=EliminationMethod.BANISHMENT,
                round=self.state.round,
            )
            self.state.eliminations.append(elim)

            narration = await self.narrator.narrate_banishment(
                banished.id, banished.role
            )
            elim_msg = EliminationMessage(
                agent_id=banished.id,
                role=banished.role,
                method=EliminationMethod.BANISHMENT,
                round=self.state.round,
                host_narration=narration,
            )
            await self.ws.broadcast(self.state.alive_player_ids, elim_msg)
            await self.ws.send(banished.id, elim_msg)
            await self._publish(
                "elimination",
                {
                    "agent_id": banished.id,
                    "role": banished.role,
                    "method": "banishment",
                    "narration": narration,
                },
            )
            self.narrator.summary.record_vote_result(
                self.state.round,
                banished.id,
                was_wolf=banished.role == Role.WEREWOLF,
                had_runoff=self._had_runoff,
            )
        else:
            self.narrator.summary.record_vote_result(
                self.state.round, None, was_wolf=False, had_runoff=False
            )

        # Build vote summary and broadcast to players + spectators
        vote_narration = await self.narrator.narrate_vote_summary(
            round_num=self.state.round,
            final_votes=self.state.banishment_votes,
            banished_team=banished.id if banished else None,
            had_runoff=self._had_runoff,
            first_round_votes=(
                self._first_round_votes if self._first_round_votes else None
            ),
        )

        first_round = self._first_round_votes if self._first_round_votes else None

        # Broadcast vote results to all alive players + the just-banished player
        vote_result_msg = VoteResultMessage(
            round=self.state.round,
            votes=self.state.banishment_votes,
            had_runoff=self._had_runoff,
            first_round_votes=first_round,
            banished_team=banished.id if banished else None,
            banished_role=banished.role.value if banished and banished.role else None,
            host_narration=vote_narration,
        )
        await self.ws.broadcast(self.state.alive_player_ids, vote_result_msg)
        if banished:
            await self.ws.send(banished.id, vote_result_msg)

        # Publish vote summary for spectators
        await self._publish(
            "vote_summary",
            {
                "round": self.state.round,
                "narration": vote_narration,
                "had_runoff": self._had_runoff,
                "first_round_votes": first_round,
                "final_votes": self.state.banishment_votes,
                "banished": (
                    {
                        "team": banished.id,
                        "role": banished.role.value if banished.role else "unknown",
                    }
                    if banished
                    else None
                ),
                "roster": self._player_roster(),
            },
        )

        await asyncio.sleep(settings.banishment_reveal_pause)

    # ------------------------------------------------------------------
    # Win check
    # ------------------------------------------------------------------

    def _check_win(self) -> bool:
        winner = self.state.check_winner()
        if winner:
            self.state.winner = winner
            self.state.phase = Phase.GAME_OVER
            return True
        return False

    async def _send_game_end(self):
        self.narrator.summary.record_game_end(self.state.winner)
        final_roles = {p.id: p.role for p in self.state.players.values()}
        narration = await self.narrator.narrate_game_end(self.state.winner, final_roles)
        msg = GameEndMessage(
            winner=self.state.winner,
            final_roles=final_roles,
            host_narration=narration,
        )
        # Send to ALL players (alive and dead)
        all_ids = list(self.state.players.keys())
        await self.ws.broadcast(all_ids, msg)
        await self._publish(
            "game_end",
            {
                "winner": self.state.winner,
                "final_roles": final_roles,
                "narration": narration,
                "chat_log": self.state.chat_log,
            },
        )

    # ------------------------------------------------------------------
    # Message collection loop
    # ------------------------------------------------------------------

    async def _collect_messages_for(self, duration: float, allowed_handler):
        """Collect and process messages for `duration` seconds."""
        deadline = asyncio.get_event_loop().time() + duration

        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                agent_id, msg = await self.ws.get_next_message(timeout=remaining)
                allowed_handler(agent_id, msg)
            except TimeoutError:
                break
            except Exception:
                logger.exception("Error processing message")

    # ------------------------------------------------------------------
    # Event publishing (spectator feed)
    # ------------------------------------------------------------------

    async def _publish(self, event_type: str, data: dict):
        channel = f"game:{self.state.game_id}:events"
        payload = json.dumps({"event": event_type, **data})
        await publish_event(channel, payload)
