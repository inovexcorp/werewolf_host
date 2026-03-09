from app.models.game import (
    EliminationMethod,
    Phase,
    Player,
    PlayerInfo,
    Role,
)
from tests.factories import make_game_state


class TestEnums:
    def test_role_values(self):
        assert Role.VILLAGER == "villager"
        assert Role.WEREWOLF == "werewolf"

    def test_phase_values(self):
        assert Phase.LOBBY == "lobby"
        assert Phase.NIGHT == "night"
        assert Phase.GAME_OVER == "game_over"

    def test_elimination_method_values(self):
        assert EliminationMethod.MURDER == "murder"
        assert EliminationMethod.BANISHMENT == "banishment"


class TestPlayer:
    def test_defaults(self):
        p = Player(id="Alpha", team="Alpha")
        assert p.role == Role.VILLAGER
        assert p.alive is True

    def test_info_property(self):
        p = Player(id="Alpha", team="Alpha", avatar_url="/img.png")
        info = p.info
        assert isinstance(info, PlayerInfo)
        assert info.id == "Alpha"
        assert info.team == "Alpha"
        assert info.avatar_url == "/img.png"


class TestGameState:
    def test_alive_players_filters_dead(self):
        gs = make_game_state(player_count=4, wolf_count=1, phase=Phase.NIGHT)
        gs.players["Team2"].alive = False
        assert len(gs.alive_players) == 3
        assert "Team2" not in gs.alive_player_ids

    def test_alive_wolves(self):
        gs = make_game_state(player_count=6, wolf_count=2)
        assert len(gs.alive_wolves) == 2
        gs.players["Team0"].alive = False
        assert len(gs.alive_wolves) == 1

    def test_alive_villagers(self):
        gs = make_game_state(player_count=6, wolf_count=1)
        assert len(gs.alive_villagers) == 5

    def test_check_winner_villagers_win(self):
        gs = make_game_state(player_count=6, wolf_count=1)
        gs.players["Team0"].alive = False  # kill the wolf
        assert gs.check_winner() == "villagers"

    def test_check_winner_werewolves_win(self):
        gs = make_game_state(player_count=6, wolf_count=1)
        # Kill villagers until wolves >= villagers
        for pid in ["Team1", "Team2", "Team3", "Team4"]:
            gs.players[pid].alive = False
        # 1 wolf, 1 villager → wolves win
        assert gs.check_winner() == "werewolves"

    def test_check_winner_one_wolf_one_villager(self):
        gs = make_game_state(player_count=2, wolf_count=1)
        assert gs.check_winner() == "werewolves"

    def test_check_winner_game_continues(self):
        gs = make_game_state(player_count=6, wolf_count=1)
        assert gs.check_winner() is None
