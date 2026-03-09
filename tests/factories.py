from app.models.game import GameState, Phase, Player, Role


def make_players(count: int = 6, wolves: int = 1) -> dict[str, Player]:
    players = {}
    for i in range(count):
        pid = f"Team{i}"
        role = Role.WEREWOLF if i < wolves else Role.VILLAGER
        players[pid] = Player(
            id=pid,
            team=pid,
            role=role,
            ws_url=f"ws://localhost:808{i}/ws",
        )
    return players


def make_game_state(
    player_count: int = 6,
    wolf_count: int = 1,
    phase: Phase = Phase.LOBBY,
) -> GameState:
    players = make_players(count=player_count, wolves=wolf_count)
    return GameState(game_id="test_game", phase=phase, players=players)
