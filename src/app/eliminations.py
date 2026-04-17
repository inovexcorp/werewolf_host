from collections.abc import Awaitable, Callable

from app.models.game import Elimination, EliminationMethod, GameState, Player
from app.models.messages import EliminationMessage
from app.ws_manager import ConnectionManager


async def eliminate_player(
    state: GameState,
    ws: ConnectionManager,
    publish: Callable[[str, dict], Awaitable[None]],
    player: Player,
    method: EliminationMethod,
    narration: str,
) -> EliminationMessage:
    """Mark dead, mute, record Elimination, broadcast EliminationMessage, publish event.

    Shared by both the morning murder path and the banishment path.
    Returns the EliminationMessage so the caller can also send it to the
    just-eliminated player (who is no longer in `alive_player_ids`).
    """
    player.alive = False
    ws.mute(player.id)
    state.eliminations.append(
        Elimination(
            agent_id=player.id,
            role=player.role,
            method=method,
            round=state.round,
        )
    )

    elim_msg = EliminationMessage(
        agent_id=player.id,
        role=player.role,
        method=method,
        round=state.round,
        host_narration=narration,
    )
    await ws.broadcast(state.alive_player_ids, elim_msg)
    await ws.send(player.id, elim_msg)
    await publish(
        "elimination",
        {
            "agent_id": player.id,
            "role": player.role,
            "method": method.value,
            "narration": narration,
        },
    )
    return elim_msg
