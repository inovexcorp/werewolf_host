import asyncio
import json
import logging

import websockets
from pydantic import TypeAdapter

from app.models.messages import (
    AgentMessage,
    ChatBroadcast,
    ErrorMessage,
    TypingIndicatorBroadcast,
    WolfChatBroadcast,
)

logger = logging.getLogger(__name__)

_agent_message_adapter = TypeAdapter(AgentMessage)


class ConnectionManager:
    def __init__(self):
        self._connections: dict[str, websockets.ClientConnection] = {}
        self._listen_tasks: dict[str, asyncio.Task] = {}
        self._message_queue: asyncio.Queue = asyncio.Queue()

    async def connect(self, agent_id: str, ws_url: str) -> bool:
        try:
            ws = await websockets.connect(ws_url, open_timeout=10)
            self._connections[agent_id] = ws
            logger.info("Connected to agent %s at %s", agent_id, ws_url)
            return True
        except Exception:
            logger.exception("Failed to connect to agent %s at %s", agent_id, ws_url)
            return False

    def start_listening(self, agent_id: str):
        if agent_id in self._listen_tasks:
            return
        task = asyncio.create_task(self._listen_loop(agent_id))
        self._listen_tasks[agent_id] = task

    async def _listen_loop(self, agent_id: str):
        ws = self._connections.get(agent_id)
        if ws is None:
            return
        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                    msg = _agent_message_adapter.validate_python(data)
                    await self._message_queue.put((agent_id, msg))
                except Exception:
                    logger.warning(
                        "Invalid message from %s: %s", agent_id, raw[:200]
                    )
                    await self.send(
                        agent_id,
                        ErrorMessage(code="INVALID_MESSAGE", message="Malformed message."),
                    )
        except websockets.ConnectionClosed:
            logger.info("Agent %s disconnected", agent_id)
        except Exception:
            logger.exception("Listen loop error for %s", agent_id)

    async def get_next_message(self, timeout: float | None = None):
        """Returns (agent_id, AgentMessage) or raises asyncio.TimeoutError."""
        if timeout is not None:
            return await asyncio.wait_for(self._message_queue.get(), timeout)
        return await self._message_queue.get()

    def drain_messages(self) -> list[tuple[str, AgentMessage]]:
        """Non-blocking drain of all queued messages."""
        messages = []
        while not self._message_queue.empty():
            try:
                messages.append(self._message_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return messages

    async def send(self, agent_id: str, message) -> bool:
        ws = self._connections.get(agent_id)
        if ws is None:
            return False
        try:
            payload = message.model_dump(mode="json", by_alias=True)
            await ws.send(json.dumps(payload))
            return True
        except Exception:
            logger.exception("Failed to send to %s", agent_id)
            return False

    async def broadcast(self, agent_ids: list[str], message):
        await asyncio.gather(
            *(self.send(aid, message) for aid in agent_ids),
            return_exceptions=True,
        )

    async def broadcast_chat(self, agent_ids: list[str], from_id: str, text: str):
        msg = ChatBroadcast(**{"from": from_id, "message": text})
        await self.broadcast(agent_ids, msg)

    async def broadcast_typing(
        self, agent_ids: list[str], from_id: str, is_typing: bool
    ):
        msg = TypingIndicatorBroadcast(agent_id=from_id, is_typing=is_typing)
        await self.broadcast(agent_ids, msg)

    async def broadcast_wolf_chat(
        self, wolf_ids: list[str], from_id: str, text: str
    ):
        msg = WolfChatBroadcast(**{"from": from_id, "message": text})
        await self.broadcast(wolf_ids, msg)

    async def disconnect(self, agent_id: str):
        task = self._listen_tasks.pop(agent_id, None)
        if task and not task.done():
            task.cancel()
        ws = self._connections.pop(agent_id, None)
        if ws:
            await ws.close()

    async def disconnect_all(self):
        for agent_id in list(self._connections.keys()):
            await self.disconnect(agent_id)
