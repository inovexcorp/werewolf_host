import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.engine import GameEngine
from app.models.game import Phase, Role
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
    engine.ws.connect = AsyncMock(return_value=True)
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
        villagers = [
            p for p in engine.state.players.values() if p.role == Role.VILLAGER
        ]
        assert len(wolves) == 1
        assert len(villagers) == 5

    def test_correct_counts_8_players(self):
        engine = _make_engine(player_count=8)
        engine._assign_roles()
        wolves = [p for p in engine.state.players.values() if p.role == Role.WEREWOLF]
        assert len(wolves) == 2


class TestResolveNightVotes:
    def test_unanimous_vote(self):
        engine = _make_engine(player_count=6, wolf_count=1)
        engine.state.night_votes = {"Team0": "Team3"}
        victim = engine._resolve_night_votes()
        assert victim is not None
        assert victim.id == "Team3"

    def test_split_vote_picks_from_top(self):
        engine = _make_engine(player_count=8, wolf_count=2)
        engine.state.night_votes = {"Team0": "Team3", "Team1": "Team4"}
        victim = engine._resolve_night_votes()
        assert victim is not None
        assert victim.id in ("Team3", "Team4")

    def test_no_votes_random_villager(self):
        engine = _make_engine(player_count=6, wolf_count=1)
        engine.state.night_votes = {}
        victim = engine._resolve_night_votes()
        assert victim is not None
        assert victim.role == Role.VILLAGER


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

    async def test_runoff_tie_random(self):
        engine = _make_engine(player_count=6, wolf_count=1)
        engine.state.banishment_votes = {
            "Team0": "Team3",
            "Team1": "Team4",
        }
        result = await engine._resolve_banishment_votes(
            is_runoff=True, candidates=["Team3", "Team4"]
        )
        assert result is not None
        assert result.id in ("Team3", "Team4")

    async def test_empty_votes(self):
        engine = _make_engine(player_count=6, wolf_count=1)
        engine.state.banishment_votes = {}
        result = await engine._resolve_banishment_votes(
            is_runoff=False, candidates=None
        )
        assert result is None


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
