from datetime import UTC, datetime
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
    host_backstory: str = ""


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
            self.timestamp = datetime.now(UTC).isoformat()


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
            self.timestamp = datetime.now(UTC).isoformat()


class VoteUpdateMessage(BaseModel):
    type: Literal["vote_update"] = "vote_update"
    votes_cast: int
    votes_total: int
    time_remaining_seconds: int


class VoteResultMessage(BaseModel):
    type: Literal["vote_result"] = "vote_result"
    round: int
    votes: dict[str, str]  # voter_team -> target_team
    had_runoff: bool = False
    first_round_votes: dict[str, str] | None = None
    banished_team: str | None = None
    banished_role: str | None = None
    host_narration: str = ""


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


class SeerResultMessage(BaseModel):
    type: Literal["seer_result"] = "seer_result"
    target: str
    role: str


class GuardResultMessage(BaseModel):
    type: Literal["guard_result"] = "guard_result"
    target: str
    protected: bool


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
    | VoteResultMessage
    | EliminationMessage
    | GameEndMessage
    | SeerResultMessage
    | GuardResultMessage
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


class AgentSeerInspect(BaseModel):
    type: Literal["seer_inspect"] = "seer_inspect"
    target: str


class AgentGuardProtect(BaseModel):
    type: Literal["guard_protect"] = "guard_protect"
    target: str


AgentMessage = Annotated[
    AgentChatMessage
    | AgentTypingIndicator
    | AgentBanishmentVote
    | AgentNightVote
    | AgentWolfChat
    | AgentSeerInspect
    | AgentGuardProtect,
    Field(discriminator="type"),
]
