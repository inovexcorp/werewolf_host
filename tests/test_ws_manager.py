import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.messages import AgentChatMessage, ErrorMessage
from app.ws_manager import ConnectionManager


class TestSend:
    async def test_send_serializes_by_alias(self):
        mgr = ConnectionManager()
        mock_ws = AsyncMock()
        mgr._connections["a1"] = mock_ws

        msg = ErrorMessage(code="TEST", message="hello")
        result = await mgr.send("a1", msg)

        assert result is True
        mock_ws.send.assert_called_once()
        payload = json.loads(mock_ws.send.call_args[0][0])
        assert payload["type"] == "error"
        assert payload["code"] == "TEST"

    async def test_send_missing_agent_returns_false(self):
        mgr = ConnectionManager()
        msg = ErrorMessage(code="TEST", message="hello")
        result = await mgr.send("missing", msg)
        assert result is False


class TestBroadcast:
    async def test_broadcast_fans_out(self):
        mgr = ConnectionManager()
        mgr._connections["a1"] = AsyncMock()
        mgr._connections["a2"] = AsyncMock()

        msg = ErrorMessage(code="X", message="y")
        await mgr.broadcast(["a1", "a2"], msg)

        mgr._connections["a1"].send.assert_called_once()
        mgr._connections["a2"].send.assert_called_once()


class TestMessageQueue:
    async def test_get_next_message(self):
        mgr = ConnectionManager()
        await mgr._message_queue.put(("a1", AgentChatMessage(message="hi")))
        agent_id, msg = await mgr.get_next_message(timeout=1.0)
        assert agent_id == "a1"
        assert msg.message == "hi"

    async def test_get_next_message_timeout(self):
        mgr = ConnectionManager()
        with pytest.raises(asyncio.TimeoutError):
            await mgr.get_next_message(timeout=0.01)

    async def test_drain_messages(self):
        mgr = ConnectionManager()
        await mgr._message_queue.put(("a1", AgentChatMessage(message="m1")))
        await mgr._message_queue.put(("a2", AgentChatMessage(message="m2")))
        drained = mgr.drain_messages()
        assert len(drained) == 2
        assert mgr._message_queue.empty()


class TestDisconnect:
    async def test_disconnect_closes_ws_and_cancels_task(self):
        mgr = ConnectionManager()
        mock_ws = AsyncMock()
        mock_task = MagicMock()
        mock_task.done.return_value = False
        mgr._connections["a1"] = mock_ws
        mgr._listen_tasks["a1"] = mock_task

        await mgr.disconnect("a1")

        mock_ws.close.assert_called_once()
        mock_task.cancel.assert_called_once()
        assert "a1" not in mgr._connections
        assert "a1" not in mgr._listen_tasks


class TestListenLoop:
    async def test_valid_json_queued(self):
        mgr = ConnectionManager()
        valid_msg = json.dumps({"type": "chat_message", "message": "hello"})

        mock_ws = AsyncMock()
        mock_ws.__aiter__ = lambda self: self
        mock_ws.__anext__ = AsyncMock(side_effect=[valid_msg, StopAsyncIteration])
        mgr._connections["a1"] = mock_ws

        # Run listen loop briefly
        await mgr._listen_loop("a1")

        assert not mgr._message_queue.empty()
        agent_id, msg = mgr._message_queue.get_nowait()
        assert agent_id == "a1"
        assert isinstance(msg, AgentChatMessage)

    async def test_invalid_json_sends_error(self):
        mgr = ConnectionManager()

        mock_ws = AsyncMock()
        mock_ws.__aiter__ = lambda self: self
        mock_ws.__anext__ = AsyncMock(
            side_effect=["not valid json{{{", StopAsyncIteration]
        )
        mgr._connections["a1"] = mock_ws

        await mgr._listen_loop("a1")

        # Queue should be empty (invalid message not queued)
        assert mgr._message_queue.empty()
        # Error should have been sent
        mock_ws.send.assert_called_once()

    async def test_no_connection_returns(self):
        mgr = ConnectionManager()
        await mgr._listen_loop("missing")
        # Should just return without error
