import pytest
from pydantic import TypeAdapter, ValidationError

from app.models.messages import (
    AgentBanishmentVote,
    AgentChatMessage,
    AgentMessage,
    ChatBroadcast,
    GameStartMessage,
    HostMessage,
    WolfChatBroadcast,
)


class TestChatBroadcastAlias:
    def test_construct_with_from_alias(self):
        msg = ChatBroadcast(**{"from": "agent_0", "message": "hello"})
        assert msg.from_ == "agent_0"

    def test_dump_uses_from_alias(self):
        msg = ChatBroadcast(**{"from": "agent_0", "message": "hello"})
        data = msg.model_dump(by_alias=True)
        assert "from" in data
        assert data["from"] == "agent_0"

    def test_auto_timestamp(self):
        msg = ChatBroadcast(**{"from": "agent_0", "message": "hi"})
        assert msg.timestamp != ""


class TestWolfChatBroadcastAlias:
    def test_construct_and_dump(self):
        msg = WolfChatBroadcast(**{"from": "wolf_1", "message": "attack!"})
        assert msg.from_ == "wolf_1"
        data = msg.model_dump(by_alias=True)
        assert data["from"] == "wolf_1"


class TestHostMessageDiscriminator:
    adapter = TypeAdapter(HostMessage)

    def test_round_trip_game_start(self):
        original = GameStartMessage(
            game_id="g1", agent_id="a1", role="villager", players=[]
        )
        data = original.model_dump(mode="json")
        parsed = self.adapter.validate_python(data)
        assert isinstance(parsed, GameStartMessage)
        assert parsed.game_id == "g1"

    def test_invalid_type_raises(self):
        with pytest.raises(ValidationError):
            self.adapter.validate_python({"type": "nonexistent"})


class TestAgentMessageDiscriminator:
    adapter = TypeAdapter(AgentMessage)

    def test_round_trip_chat(self):
        original = AgentChatMessage(message="hello")
        data = original.model_dump(mode="json")
        parsed = self.adapter.validate_python(data)
        assert isinstance(parsed, AgentChatMessage)

    def test_round_trip_vote(self):
        original = AgentBanishmentVote(target="agent_1")
        data = original.model_dump(mode="json")
        parsed = self.adapter.validate_python(data)
        assert isinstance(parsed, AgentBanishmentVote)

    def test_invalid_type_raises(self):
        with pytest.raises(ValidationError):
            self.adapter.validate_python({"type": "bogus"})
