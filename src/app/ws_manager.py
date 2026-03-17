import asyncio
import contextlib
import json
import logging

from pydantic import TypeAdapter
from starlette.websockets import WebSocket, WebSocketDisconnect

from app.models.messages import (
    AgentMessage,
    ChatBroadcast,
    ErrorMessage,
    TypingIndicatorBroadcast,
    WolfChatBroadcast,
)

logger = logging.getLogger(__name__)

_agent_message_adapter = TypeAdapter(AgentMessage)

# ---------------------------------------------------------------------------
# Module-level pending connection pool
# ---------------------------------------------------------------------------

_pending_connections: dict[str, WebSocket] = {}
_connect_events: dict[str, asyncio.Event] = {}
_connected_agents: set[str] = set()


def clear_pending() -> None:
    """Reset global connection pool between games."""
    _pending_connections.clear()
    _connect_events.clear()
    _connected_agents.clear()


def agent_connected(agent_id: str, ws: WebSocket) -> None:
    """Called by the WS endpoint when an agent connects."""
    _pending_connections[agent_id] = ws
    _connected_agents.add(agent_id)
    event = _connect_events.get(agent_id)
    if event:
        event.set()


def agent_disconnected(agent_id: str) -> None:
    """Called when an agent's WebSocket closes."""
    _connected_agents.discard(agent_id)


def get_connected_agents() -> set[str]:
    """Return a copy of the currently connected agent IDs."""
    return set(_connected_agents)


def expect_agent(agent_id: str) -> None:
    """Prepare to wait for a specific agent.

    Sets event immediately if already connected.
    """
    if agent_id not in _connect_events:
        _connect_events[agent_id] = asyncio.Event()
    if agent_id in _pending_connections:
        _connect_events[agent_id].set()


async def wait_for_agent(agent_id: str, timeout: float) -> WebSocket | None:
    """Wait for an agent to connect, returning its WebSocket or None on timeout."""
    event = _connect_events.get(agent_id)
    if event is None:
        return None
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
        return _pending_connections.pop(agent_id, None)
    except TimeoutError:
        return None
    finally:
        _connect_events.pop(agent_id, None)


class ConnectionManager:
    def __init__(self):
        self._connections: dict[str, WebSocket] = {}
        self._listen_tasks: dict[str, asyncio.Task] = {}
        self._message_queue: asyncio.Queue = asyncio.Queue()

    def register_connection(self, agent_id: str, ws: WebSocket) -> None:
        """Store an already-accepted inbound WebSocket."""
        self._connections[agent_id] = ws
        logger.info("Registered inbound connection for agent %s", agent_id)

    async def wait_for_connections(
        self, agent_ids: list[str], timeout: float = 60
    ) -> list[str]:
        """Wait for all agents to connect inbound.

        Returns list of agent_ids that failed.
        """
        for agent_id in agent_ids:
            expect_agent(agent_id)

        failures = []
        for agent_id in agent_ids:
            ws = await wait_for_agent(agent_id, timeout=timeout)
            if ws is None:
                failures.append(agent_id)
            else:
                self.register_connection(agent_id, ws)
        return failures

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
            while True:
                raw = await ws.receive_text()
                try:
                    data = json.loads(raw)
                    msg = _agent_message_adapter.validate_python(data)
                    await self._message_queue.put((agent_id, msg))
                except Exception:
                    logger.warning("Invalid message from %s: %s", agent_id, raw[:200])
                    await self.send(
                        agent_id,
                        ErrorMessage(
                            code="INVALID_MESSAGE", message="Malformed message."
                        ),
                    )
        except WebSocketDisconnect:
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
            await ws.send_text(json.dumps(payload))
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

    async def broadcast_wolf_chat(self, wolf_ids: list[str], from_id: str, text: str):
        msg = WolfChatBroadcast(**{"from": from_id, "message": text})
        await self.broadcast(wolf_ids, msg)

    async def disconnect(self, agent_id: str):
        task = self._listen_tasks.pop(agent_id, None)
        if task and not task.done():
            task.cancel()
        ws = self._connections.pop(agent_id, None)
        if ws:
            with contextlib.suppress(Exception):
                await ws.close()

    async def disconnect_all(self):
        for agent_id in list(self._connections.keys()):
            await self.disconnect(agent_id)
