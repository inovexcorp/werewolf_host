from app.models.game import GameState, Phase, Player, Role


def make_players(
    count: int = 6, wolves: int = 1, seers: int = 0, guards: int = 0
) -> dict[str, Player]:
    players = {}
    for i in range(count):
        pid = f"Team{i}"
        if i < wolves:
            role = Role.WEREWOLF
        elif i < wolves + seers:
            role = Role.SEER
        elif i < wolves + seers + guards:
            role = Role.GUARD
        else:
            role = Role.VILLAGER
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
    seer_count: int = 0,
    guard_count: int = 0,
    phase: Phase = Phase.LOBBY,
) -> GameState:
    players = make_players(
        count=player_count,
        wolves=wolf_count,
        seers=seer_count,
        guards=guard_count,
    )
    return GameState(game_id="test_game", phase=phase, players=players)
