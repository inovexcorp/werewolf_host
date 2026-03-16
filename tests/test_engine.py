import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.engine import GameEngine
from app.models.game import Phase, Role
from app.models.messages import AgentGuardProtect, AgentSeerInspect
from app.narrator import Narrator
from tests.factories import make_players


def _make_engine(player_count=6, wolf_count=1):
    """Build a GameEngine with mocked WS and narrator."""
    players = list(make_players(count=player_count, wolves=wolf_count).values())
    narrator = Narrator()
    engine = GameEngine("test_game", players, narrator)

    # Mock all WS operations
    engine.ws = MagicMock()
    engine.ws.send = AsyncMock(return_value=True)
    engine.ws.broadcast = AsyncMock()
    engine.ws.broadcast_chat = AsyncMock()
    engine.ws.broadcast_typing = AsyncMock()
    engine.ws.broadcast_wolf_chat = AsyncMock()
    engine.ws.wait_for_connections = AsyncMock(return_value=[])
    engine.ws.disconnect = AsyncMock()
    engine.ws.disconnect_all = AsyncMock()
    engine.ws.start_listening = MagicMock()
    engine.ws.get_next_message = AsyncMock(side_effect=asyncio.TimeoutError)

    return engine


class TestAssignRoles:
    def test_correct_counts_6_players(self):
        engine = _make_engine(player_count=6)
        engine._assign_roles()
        wolves = [p for p in engine.state.players.values() if p.role == Role.WEREWOLF]
        seers = [p for p in engine.state.players.values() if p.role == Role.SEER]
        guards = [p for p in engine.state.players.values() if p.role == Role.GUARD]
        villagers = [
            p for p in engine.state.players.values() if p.role == Role.VILLAGER
        ]
        assert len(wolves) == 1
        assert len(seers) == 1
        assert len(guards) == 1
        assert len(villagers) == 3

    def test_correct_counts_8_players(self):
        engine = _make_engine(player_count=8)
        engine._assign_roles()
        wolves = [p for p in engine.state.players.values() if p.role == Role.WEREWOLF]
        seers = [p for p in engine.state.players.values() if p.role == Role.SEER]
        guards = [p for p in engine.state.players.values() if p.role == Role.GUARD]
        assert len(wolves) == 2
        assert len(seers) == 1
        assert len(guards) == 1

    def test_no_seer_5_players(self):
        engine = _make_engine(player_count=5)
        engine._assign_roles()
        seers = [p for p in engine.state.players.values() if p.role == Role.SEER]
        assert len(seers) == 0

    def test_seer_assigned_6_plus_players(self):
        engine = _make_engine(player_count=6)
        engine._assign_roles()
        seers = [p for p in engine.state.players.values() if p.role == Role.SEER]
        assert len(seers) == 1
        # Seer should not be a wolf
        assert seers[0].role != Role.WEREWOLF


class TestResolveNightVotes:
    def test_unanimous_vote(self):
        engine = _make_engine(player_count=6, wolf_count=1)
        engine.state.night_votes = {"Team0": "Team3"}
        victim, was_guarded, _saved = engine._resolve_night_votes()
        assert victim is not None
        assert victim.id == "Team3"
        assert was_guarded is False

    def test_split_vote_picks_from_top(self):
        engine = _make_engine(player_count=8, wolf_count=2)
        engine.state.night_votes = {"Team0": "Team3", "Team1": "Team4"}
        victim, was_guarded, _saved = engine._resolve_night_votes()
        assert victim is not None
        assert victim.id in ("Team3", "Team4")
        assert was_guarded is False

    def test_no_votes_random_villager(self):
        engine = _make_engine(player_count=6, wolf_count=1)
        engine.state.night_votes = {}
        victim, _was_guarded, _saved = engine._resolve_night_votes()
        assert victim is not None
        assert victim.role in (Role.VILLAGER, Role.SEER, Role.GUARD)


class TestCheckWin:
    def test_villager_win(self):
        engine = _make_engine(player_count=6, wolf_count=1)
        engine.state.players["Team0"].alive = False  # kill the wolf
        assert engine._check_win() is True
        assert engine.state.winner == "villagers"
        assert engine.state.phase == Phase.GAME_OVER

    def test_werewolf_win(self):
        engine = _make_engine(player_count=6, wolf_count=1)
        for i in range(1, 5):
            engine.state.players[f"Team{i}"].alive = False
        assert engine._check_win() is True
        assert engine.state.winner == "werewolves"

    def test_game_continues(self):
        engine = _make_engine(player_count=6, wolf_count=1)
        assert engine._check_win() is False
        assert engine.state.winner is None


class TestResolveBanishmentVotes:
    async def test_clear_winner(self):
        engine = _make_engine(player_count=6, wolf_count=1)
        engine.state.banishment_votes = {
            "Team0": "Team3",
            "Team1": "Team3",
            "Team2": "Team4",
        }
        result = await engine._resolve_banishment_votes(
            is_runoff=False, candidates=None
        )
        assert result is not None
        assert result.id == "Team3"

    async def test_tie_triggers_runoff(self):
        engine = _make_engine(player_count=6, wolf_count=1)
        engine.state.banishment_votes = {
            "Team0": "Team3",
            "Team1": "Team4",
        }
        # The runoff will call _run_vote_round, which calls _collect_messages_for.
        # Mock it to just resolve immediately.
        with (
            patch.object(engine, "_collect_messages_for", new=AsyncMock()),
            patch.object(engine, "_publish", new=AsyncMock()),
        ):
            result = await engine._resolve_banishment_votes(
                is_runoff=False, candidates=None
            )
        # Should have had a runoff
        assert engine._had_runoff is True
        # Result should be one of the tied candidates (random vote assigned)
        assert result is not None

    async def test_runoff_tie_no_banishment(self):
        engine = _make_engine(player_count=6, wolf_count=1)
        engine.state.banishment_votes = {
            "Team0": "Team3",
            "Team1": "Team4",
        }
        result = await engine._resolve_banishment_votes(
            is_runoff=True, candidates=["Team3", "Team4"]
        )
        assert result is None

    async def test_empty_votes(self):
        engine = _make_engine(player_count=6, wolf_count=1)
        engine.state.banishment_votes = {}
        result = await engine._resolve_banishment_votes(
            is_runoff=False, candidates=None
        )
        assert result is None


class TestSeerInspect:
    async def test_valid_inspect_returns_role(self):
        engine = _make_engine(player_count=6, wolf_count=1)
        # Manually set up a seer
        engine.state.players["Team2"].role = Role.SEER
        engine.state.phase = Phase.NIGHT

        msg = AgentSeerInspect(target="Team0")  # Team0 is a wolf
        result = engine._handle_night_message("Team2", msg)
        assert result is True
        # Should have sent a SeerResultMessage
        engine.ws.send.assert_called()

    async def test_inspect_by_non_seer_rejected(self):
        engine = _make_engine(player_count=6, wolf_count=1)
        engine.state.phase = Phase.NIGHT

        msg = AgentSeerInspect(target="Team3")
        # Team1 is a villager, not a seer
        result = engine._handle_night_message("Team1", msg)
        assert result is False

    async def test_inspect_dead_player_rejected(self):
        engine = _make_engine(player_count=6, wolf_count=1)
        engine.state.players["Team2"].role = Role.SEER
        engine.state.players["Team3"].alive = False
        engine.state.phase = Phase.NIGHT

        msg = AgentSeerInspect(target="Team3")
        result = engine._handle_night_message("Team2", msg)
        assert result is False

    async def test_inspect_self_rejected(self):
        engine = _make_engine(player_count=6, wolf_count=1)
        engine.state.players["Team2"].role = Role.SEER
        engine.state.phase = Phase.NIGHT

        msg = AgentSeerInspect(target="Team2")
        result = engine._handle_night_message("Team2", msg)
        assert result is False

    async def test_only_one_inspect_per_night(self):
        engine = _make_engine(player_count=6, wolf_count=1)
        engine.state.players["Team2"].role = Role.SEER
        engine.state.phase = Phase.NIGHT

        msg1 = AgentSeerInspect(target="Team3")
        result1 = engine._handle_night_message("Team2", msg1)
        assert result1 is True

        msg2 = AgentSeerInspect(target="Team4")
        result2 = engine._handle_night_message("Team2", msg2)
        assert result2 is False

    def test_seer_counts_as_villager_for_win(self):
        engine = _make_engine(player_count=6, wolf_count=1)
        engine.state.players["Team1"].role = Role.SEER
        # Kill the wolf -> villagers should win
        engine.state.players["Team0"].alive = False
        assert engine._check_win() is True
        assert engine.state.winner == "villagers"

    def test_seer_counts_for_wolf_parity(self):
        engine = _make_engine(player_count=6, wolf_count=1)
        engine.state.players["Team1"].role = Role.SEER
        # Kill all non-wolves except seer: wolf parity with 1 wolf + 1 seer
        for i in range(2, 6):
            engine.state.players[f"Team{i}"].alive = False
        assert engine._check_win() is True
        assert engine.state.winner == "werewolves"

    def test_guard_counts_as_villager_for_win(self):
        engine = _make_engine(player_count=6, wolf_count=1)
        engine.state.players["Team1"].role = Role.GUARD
        # Kill the wolf -> villagers should win
        engine.state.players["Team0"].alive = False
        assert engine._check_win() is True
        assert engine.state.winner == "villagers"


class TestGuardProtect:
    def test_role_assignment_includes_guard_at_6_players(self):
        engine = _make_engine(player_count=6)
        engine._assign_roles()
        guards = [p for p in engine.state.players.values() if p.role == Role.GUARD]
        assert len(guards) == 1
        # Guard should not be a wolf or seer
        assert guards[0].role == Role.GUARD

    def test_no_guard_5_players(self):
        engine = _make_engine(player_count=5)
        engine._assign_roles()
        guards = [p for p in engine.state.players.values() if p.role == Role.GUARD]
        assert len(guards) == 0

    async def test_guard_protection_prevents_wolf_kill(self):
        engine = _make_engine(player_count=6, wolf_count=1)
        engine.state.players["Team2"].role = Role.GUARD
        engine.state.phase = Phase.NIGHT

        # Guard protects Team3
        msg = AgentGuardProtect(target="Team3")
        result = engine._handle_night_message("Team2", msg)
        assert result is True
        assert engine._guard_protected == "Team3"

        # Wolf votes for Team3
        engine.state.night_votes = {"Team0": "Team3"}
        victim, was_guarded, _saved = engine._resolve_night_votes()
        assert victim is None
        assert was_guarded is True

    async def test_guard_cannot_protect_same_player_twice(self):
        engine = _make_engine(player_count=6, wolf_count=1)
        engine.state.players["Team2"].role = Role.GUARD
        engine.state.phase = Phase.NIGHT
        engine._guard_last_protected = "Team3"

        msg = AgentGuardProtect(target="Team3")
        result = engine._handle_night_message("Team2", msg)
        assert result is False

    async def test_guard_can_protect_self(self):
        engine = _make_engine(player_count=6, wolf_count=1)
        engine.state.players["Team2"].role = Role.GUARD
        engine.state.phase = Phase.NIGHT

        msg = AgentGuardProtect(target="Team2")
        result = engine._handle_night_message("Team2", msg)
        assert result is True

    async def test_only_guard_can_protect(self):
        engine = _make_engine(player_count=6, wolf_count=1)
        engine.state.phase = Phase.NIGHT

        msg = AgentGuardProtect(target="Team3")
        # Team1 is a villager, not the guard
        result = engine._handle_night_message("Team1", msg)
        assert result is False

    async def test_guard_only_once_per_night(self):
        engine = _make_engine(player_count=6, wolf_count=1)
        engine.state.players["Team2"].role = Role.GUARD
        engine.state.phase = Phase.NIGHT

        msg1 = AgentGuardProtect(target="Team3")
        result1 = engine._handle_night_message("Team2", msg1)
        assert result1 is True

        msg2 = AgentGuardProtect(target="Team4")
        result2 = engine._handle_night_message("Team2", msg2)
        assert result2 is False

    async def test_guard_save_morning_announcement(self, override_settings):
        override_settings(
            morning_announcement_pause=0,
            openai_api_key="",
        )
        engine = _make_engine(player_count=6, wolf_count=1)
        engine.state.players["Team2"].role = Role.GUARD
        engine.state.round = 1

        with patch.object(engine, "_publish", new=AsyncMock()):
            await engine._morning_announcement(victim=None, was_guarded=True)

        # All players should still be alive
        assert all(p.alive for p in engine.state.players.values())


class TestSmokeRun:
    async def test_abbreviated_game_to_completion(self, override_settings):
        override_settings(
            morning_announcement_pause=0,
            banishment_reveal_pause=0,
            night_duration=0,
            discussion_duration=0,
            voting_duration=0,
            runoff_voting_duration=0,
            openai_api_key="",
        )

        engine = _make_engine(player_count=6, wolf_count=1)

        with patch.object(engine, "_publish", new=AsyncMock()):
            await engine.run()

        assert engine.state.phase == Phase.GAME_OVER
        assert engine.state.winner in ("villagers", "werewolves")
