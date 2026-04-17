import logging
from typing import TYPE_CHECKING

from app.models.game import Player, Role
from app.models.messages import (
    AgentGuardProtect,
    AgentNightVote,
    AgentSeerInspect,
    AgentTypingIndicator,
    AgentWolfChat,
    ErrorMessage,
    GuardResultMessage,
    SeerResultMessage,
)

if TYPE_CHECKING:
    from app.engine import GameEngine

logger = logging.getLogger(__name__)


class NightMessageHandler:
    """Dispatches inbound agent messages during the night phase.

    Owns the per-role validation logic (wolf vote, wolf chat, guard protect,
    seer inspect, typing indicators). Guard/seer state (_guard_protected,
    _guard_last_protected, _guard_acted, _seer_inspected) lives on the engine
    so the tests can introspect it; the handler reads and writes it through
    its engine reference.
    """

    def __init__(self, engine: "GameEngine"):
        self.engine = engine

    async def handle(self, agent_id: str, msg) -> bool:
        player = self.engine.state.players.get(agent_id)
        if not player or not player.alive:
            return False

        if isinstance(msg, AgentNightVote):
            return await self._handle_night_vote(agent_id, player, msg)
        if isinstance(msg, AgentWolfChat):
            return await self._handle_wolf_chat(agent_id, player, msg)
        if isinstance(msg, AgentGuardProtect):
            return await self._handle_guard_protect(agent_id, player, msg)
        if isinstance(msg, AgentSeerInspect):
            return await self._handle_seer_inspect(agent_id, player, msg)
        if isinstance(msg, AgentTypingIndicator):
            return await self._handle_typing(agent_id, player, msg)
        return False

    async def _send_error(self, agent_id: str, code: str, message: str) -> None:
        await self.engine.ws.send(agent_id, ErrorMessage(code=code, message=message))

    async def _handle_night_vote(
        self, agent_id: str, player: Player, msg: AgentNightVote
    ) -> bool:
        state = self.engine.state
        if player.role != Role.WEREWOLF:
            logger.info(
                "Rejecting night_vote from non-wolf agent_id=%s role=%s",
                agent_id,
                player.role,
            )
            await self._send_error(
                agent_id, "NOT_ALLOWED", "You are not allowed to perform that action."
            )
            return False
        if msg.target not in state.alive_player_ids or msg.target == agent_id:
            await self._send_error(agent_id, "INVALID_TARGET", "Invalid target.")
            return False
        target = state.players.get(msg.target)
        if target and target.role == Role.WEREWOLF:
            await self._send_error(
                agent_id, "INVALID_TARGET", "Cannot target a fellow werewolf."
            )
            return False
        state.night_votes[agent_id] = msg.target
        return True

    async def _handle_wolf_chat(
        self, agent_id: str, player: Player, msg: AgentWolfChat
    ) -> bool:
        state = self.engine.state
        if player.role != Role.WEREWOLF:
            logger.info(
                "Rejecting wolf_chat from non-wolf agent_id=%s role=%s",
                agent_id,
                player.role,
            )
            await self._send_error(
                agent_id, "NOT_ALLOWED", "You are not allowed to perform that action."
            )
            return False
        wolf_ids = [p.id for p in state.alive_wolves]
        self.engine._fire_and_forget(
            self.engine.ws.broadcast_wolf_chat(wolf_ids, agent_id, msg.message)
        )
        self.engine._fire_and_forget(
            self.engine._publish(
                "wolf_chat_message",
                {
                    "from": agent_id,
                    "message": msg.message,
                    "round": state.round,
                },
            )
        )
        state.chat_log.append(
            {
                "channel": "wolf",
                "from": agent_id,
                "message": msg.message,
                "round": state.round,
                "phase": "night",
            }
        )
        return True

    async def _handle_guard_protect(
        self, agent_id: str, player: Player, msg: AgentGuardProtect
    ) -> bool:
        state = self.engine.state
        if player.role != Role.GUARD:
            logger.info(
                "Rejecting guard_protect from non-guard agent_id=%s role=%s",
                agent_id,
                player.role,
            )
            await self._send_error(
                agent_id, "NOT_ALLOWED", "You are not allowed to perform that action."
            )
            return False
        if self.engine._guard_acted:
            await self._send_error(
                agent_id,
                "ALREADY_PROTECTED",
                "You have already protected someone this night.",
            )
            return False
        if msg.target not in state.alive_player_ids:
            await self._send_error(agent_id, "INVALID_TARGET", "Invalid target.")
            return False
        if msg.target == self.engine._guard_last_protected:
            await self._send_error(
                agent_id, "SAME_TARGET", "Cannot protect the same player twice."
            )
            return False

        self.engine._guard_protected = msg.target
        self.engine._guard_acted = True
        await self.engine.ws.send(
            agent_id, GuardResultMessage(target=msg.target, protected=True)
        )
        self.engine._fire_and_forget(
            self.engine._publish(
                "guard_protect",
                {
                    "guard": agent_id,
                    "target": msg.target,
                    "round": state.round,
                },
            )
        )
        state.chat_log.append(
            {
                "channel": "guard",
                "from": agent_id,
                "message": f"Protecting {msg.target}",
                "round": state.round,
                "phase": "night",
            }
        )
        return True

    async def _handle_seer_inspect(
        self, agent_id: str, player: Player, msg: AgentSeerInspect
    ) -> bool:
        state = self.engine.state
        if player.role != Role.SEER:
            logger.info(
                "Rejecting seer_inspect from non-seer agent_id=%s role=%s",
                agent_id,
                player.role,
            )
            await self._send_error(
                agent_id, "NOT_ALLOWED", "You are not allowed to perform that action."
            )
            return False
        if self.engine._seer_inspected:
            await self._send_error(
                agent_id,
                "ALREADY_INSPECTED",
                "You have already inspected someone this night.",
            )
            return False
        if msg.target not in state.alive_player_ids or msg.target == agent_id:
            await self._send_error(agent_id, "INVALID_TARGET", "Invalid target.")
            return False

        target_player = state.players[msg.target]
        self.engine._seer_inspected = True
        await self.engine.ws.send(
            agent_id,
            SeerResultMessage(target=msg.target, role=target_player.role.value),
        )
        self.engine._fire_and_forget(
            self.engine._publish(
                "seer_inspect",
                {
                    "seer": agent_id,
                    "target": msg.target,
                    "role": target_player.role.value,
                    "round": state.round,
                },
            )
        )
        state.chat_log.append(
            {
                "channel": "seer",
                "from": agent_id,
                "message": f"Inspected {msg.target}: {target_player.role.value}",
                "round": state.round,
                "phase": "night",
            }
        )
        return True

    async def _handle_typing(
        self, agent_id: str, player: Player, msg: AgentTypingIndicator
    ) -> bool:
        if player.role == Role.WEREWOLF:
            wolf_ids = [
                p.id for p in self.engine.state.alive_wolves if p.id != agent_id
            ]
            self.engine._fire_and_forget(
                self.engine.ws.broadcast_typing(wolf_ids, agent_id, msg.is_typing)
            )
        return True
