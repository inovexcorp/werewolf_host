from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.models.game import PlayerInfo, Role


# ---------------------------------------------------------------------------
# Host -> Agent messages
# ---------------------------------------------------------------------------

class GameStartMessage(BaseModel):
    type: Literal["game_start"] = "game_start"
    game_id: str
    agent_id: str
    role: Role
    players: list[PlayerInfo]
    private_info: dict = {}
    host_narration: str = ""


class PhaseChangeMessage(BaseModel):
    type: Literal["phase_change"] = "phase_change"
    phase: str
    round: int
    time_remaining_seconds: int
    alive_players: list[str]
    host_narration: str = ""


class ChatBroadcast(BaseModel):
    type: Literal["chat_message"] = "chat_message"
    from_: str = Field(alias="from", serialization_alias="from")
    message: str
    timestamp: str = ""

    def model_post_init(self, _context):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


class TypingIndicatorBroadcast(BaseModel):
    type: Literal["typing_indicator"] = "typing_indicator"
    agent_id: str
    is_typing: bool


class WolfChatBroadcast(BaseModel):
    type: Literal["wolf_chat_message"] = "wolf_chat_message"
    from_: str = Field(alias="from", serialization_alias="from")
    message: str
    timestamp: str = ""

    def model_post_init(self, _context):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


class VoteUpdateMessage(BaseModel):
    type: Literal["vote_update"] = "vote_update"
    votes_cast: int
    votes_total: int
    time_remaining_seconds: int


class EliminationMessage(BaseModel):
    type: Literal["elimination"] = "elimination"
    agent_id: str
    role: Role
    method: str
    round: int
    host_narration: str = ""


class GameEndMessage(BaseModel):
    type: Literal["game_end"] = "game_end"
    winner: str
    final_roles: dict[str, str]
    host_narration: str = ""


class ErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    code: str
    message: str


HostMessage = Annotated[
    GameStartMessage
    | PhaseChangeMessage
    | ChatBroadcast
    | TypingIndicatorBroadcast
    | WolfChatBroadcast
    | VoteUpdateMessage
    | EliminationMessage
    | GameEndMessage
    | ErrorMessage,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Agent -> Host messages
# ---------------------------------------------------------------------------

class AgentChatMessage(BaseModel):
    type: Literal["chat_message"] = "chat_message"
    message: str


class AgentTypingIndicator(BaseModel):
    type: Literal["typing_indicator"] = "typing_indicator"
    is_typing: bool


class AgentBanishmentVote(BaseModel):
    type: Literal["banishment_vote"] = "banishment_vote"
    target: str


class AgentNightVote(BaseModel):
    type: Literal["night_vote"] = "night_vote"
    target: str


class AgentWolfChat(BaseModel):
    type: Literal["wolf_chat_message"] = "wolf_chat_message"
    message: str


AgentMessage = Annotated[
    AgentChatMessage
    | AgentTypingIndicator
    | AgentBanishmentVote
    | AgentNightVote
    | AgentWolfChat,
    Field(discriminator="type"),
]
