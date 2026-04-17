import asyncio
import json
import logging
import random

from app import phases
from app.config import settings, wolves_for_player_count
from app.models.game import (
    GameState,
    Phase,
    Player,
    Role,
)
from app.models.messages import (
    GameEndMessage,
    GameStartMessage,
)
from app.narrator import Narrator
from app.night_handler import NightMessageHandler
from app.redis import publish_event
from app.scoring import award_points
from app.voting import resolve_night_votes, tally_banishment_votes
from app.ws_manager import ConnectionManager, clear_pending

logger = logging.getLogger(__name__)

NARRATOR_ID = "narrator"


class GameEngine:
    def __init__(
        self,
        game_id: str,
        players: list[Player],
        narrator: Narrator,
        host_backstory: str = "",
        series_id: str | None = None,
    ):
        self.state = GameState(game_id=game_id, series_id=series_id)
        self.series_id = series_id
        self.ws = ConnectionManager()
        self.narrator = narrator
        self.host_backstory = host_backstory
        self._phase_task: asyncio.Task | None = None
        self._had_runoff: bool = False
        self._first_round_votes: dict[str, str] = {}
        self._seer_inspected: bool = False
        self._guard_protected: str | None = None
        self._guard_last_protected: str | None = None
        self._guard_acted: bool = False
        self._background_tasks: set[asyncio.Task] = set()

        for p in players:
            self.state.players[p.id] = p

        self._night_handler = NightMessageHandler(self)

    def _fire_and_forget(self, coro) -> None:
        """Create a background task and prevent it from being garbage collected."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def setup(self) -> list[str]:
        """Wait for all agents to connect inbound."""
        agent_ids = list(self.state.players.keys())
        return await self.ws.wait_for_connections(agent_ids, timeout=60)

    async def run(self):
        """Main game loop. Call after setup()."""
        self._assign_roles()
        await self._send_game_start()

        if settings.introduction_duration > 0:
            await self._introduction_phase()

        while not self.state.winner:
            self.state.round += 1

            await self._night_phase()
            victim, was_guarded, saved_player_id = self._resolve_night_votes()
            await self._morning_announcement(victim, was_guarded, saved_player_id)

            if self._check_win():
                break

            await self._discussion_phase()
            banished = await self._voting_phase()
            await self._banishment_reveal(banished)

            self._check_win()

        await self._send_game_end()
        await award_points(self.state)
        await self.ws.disconnect_all()
        clear_pending()  # reset globals so next game gets fresh state

    # ------------------------------------------------------------------
    # Role assignment
    # ------------------------------------------------------------------

    def _assign_roles(self):
        player_list = list(self.state.players.values())
        n_wolves = wolves_for_player_count(len(player_list))
        random.shuffle(player_list)

        for i, p in enumerate(player_list):
            p.role = Role.WEREWOLF if i < n_wolves else Role.VILLAGER

        # Assign one Seer if enough players
        if len(player_list) >= settings.seer_player_threshold:
            non_wolves = [p for p in player_list if p.role != Role.WEREWOLF]
            if non_wolves:
                non_wolves[0].role = Role.SEER

        # Assign one Guard if enough players
        if len(player_list) >= settings.guard_player_threshold:
            eligible = [
                p for p in player_list if p.role not in (Role.WEREWOLF, Role.SEER)
            ]
            if eligible:
                eligible[0].role = Role.GUARD

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

        game_start_data = {
            "narration": narration,
            "players": [
                {
                    "id": p.id,
                    "team": p.team,
                    "avatar_url": p.avatar_url,
                    "role": p.role,
                }
                for p in self.state.players.values()
            ],
        }
        if self.series_id is not None:
            game_start_data["series_id"] = self.series_id
        await self._publish("game_start", game_start_data)

    # ------------------------------------------------------------------
    # Phase methods: delegators to phases.py. Kept on engine to preserve
    # the test contract (tests/test_engine.py calls these by name).
    # ------------------------------------------------------------------

    async def _introduction_phase(self):
        await phases.run_introduction_phase(self)

    async def _night_phase(self):
        await phases.run_night_phase(self)

    def _handle_night_message(self, agent_id: str, msg) -> bool:
        return self._night_handler.handle(agent_id, msg)

    def _resolve_night_votes(self) -> tuple[Player | None, bool, str | None]:
        """Resolve night votes. Returns (victim, was_guarded, saved_player_id)."""
        result = resolve_night_votes(self.state, self._guard_protected)
        self._guard_last_protected = self._guard_protected
        return result.victim, result.was_guarded, result.saved_player_id

    async def _morning_announcement(
        self,
        victim: Player | None,
        was_guarded: bool = False,
        saved_player_id: str | None = None,
    ):
        await phases.morning_announcement(self, victim, was_guarded, saved_player_id)

    async def _discussion_phase(self):
        await phases.run_discussion_phase(self)

    async def _voting_phase(self) -> Player | None:
        return await phases.run_voting_phase(self)

    async def _resolve_banishment_votes(
        self, is_runoff: bool, candidates: list[str] | None
    ) -> Player | None:
        tally = tally_banishment_votes(self.state.banishment_votes)

        if tally.winner_id is not None:
            return self.state.players.get(tally.winner_id)

        if not tally.tied_ids or is_runoff:
            # No votes, or a second consecutive tie → no one is banished.
            return None

        self._had_runoff = True
        return await phases._run_vote_round(
            self,
            settings.runoff_voting_duration,
            is_runoff=True,
            candidates=tally.tied_ids,
        )

    async def _banishment_reveal(self, banished: Player | None):
        await phases.banishment_reveal(self, banished)

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
        game_end_data = {
            "winner": self.state.winner,
            "final_roles": final_roles,
            "narration": narration,
            "chat_log": self.state.chat_log,
        }
        if self.series_id is not None:
            game_end_data["series_id"] = self.series_id
        await self._publish("game_end", game_end_data)

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
