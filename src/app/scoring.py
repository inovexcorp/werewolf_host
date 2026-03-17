from app.config import settings
from app.models.game import GameState, Role
from app.redis import get_redis


async def award_points(state: GameState) -> dict[str, int]:
    """Calculate and award points for a completed game.

    Returns {team: points_awarded}.
    """
    if not state.winner:
        return {}

    awards: dict[str, int] = {}

    for player in state.alive_players:
        if state.winner == "werewolves" and player.role == Role.WEREWOLF:
            awards[player.id] = (
                awards.get(player.id, 0) + settings.scoring_wolf_win_points
            )
        elif state.winner == "villagers" and player.role == Role.VILLAGER:
            alive_villager_count = len(state.alive_villagers)
            awards[player.id] = awards.get(player.id, 0) + (
                settings.scoring_villager_win_points * alive_villager_count
            )

    # Update Redis sorted set
    if awards:
        r = await get_redis()
        for team, points in awards.items():
            await r.zincrby("scoreboard", points, team)

        # Series-scoped scoreboard
        series_id = await r.get(f"game_series:{state.game_id}")
        if series_id:
            for team, points in awards.items():
                await r.zincrby(f"series:{series_id}:scoreboard", points, team)

    return awards
