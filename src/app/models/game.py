from enum import StrEnum

from pydantic import BaseModel


class Role(StrEnum):
    VILLAGER = "villager"
    WEREWOLF = "werewolf"
    SEER = "seer"
    GUARD = "guard"


class Phase(StrEnum):
    LOBBY = "lobby"
    INTRODUCTION = "introduction"
    NIGHT = "night"
    MORNING = "morning"
    DISCUSSION = "discussion"
    VOTING = "voting"
    RUNOFF_VOTING = "runoff_voting"
    BANISHMENT = "banishment"
    GAME_OVER = "game_over"


class EliminationMethod(StrEnum):
    MURDER = "murder"
    BANISHMENT = "banishment"


class PlayerInfo(BaseModel):
    id: str
    team: str
    avatar_url: str = ""


class Player(BaseModel):
    id: str
    team: str
    role: Role = Role.VILLAGER
    alive: bool = True
    avatar_url: str = ""

    @property
    def info(self) -> PlayerInfo:
        return PlayerInfo(id=self.id, team=self.team, avatar_url=self.avatar_url)


class Elimination(BaseModel):
    agent_id: str
    role: Role
    method: EliminationMethod
    round: int


class GameState(BaseModel):
    game_id: str
    series_id: str | None = None
    phase: Phase = Phase.LOBBY
    round: int = 0
    players: dict[str, Player] = {}
    eliminations: list[Elimination] = []
    winner: str | None = None

    # Current-phase transient state
    night_votes: dict[str, str] = {}  # wolf_id -> target_id
    banishment_votes: dict[str, str] = {}  # voter_id -> target_id
    chat_log: list[dict] = []

    @property
    def alive_players(self) -> list[Player]:
        return [p for p in self.players.values() if p.alive]

    @property
    def alive_player_ids(self) -> list[str]:
        return [p.id for p in self.alive_players]

    @property
    def alive_wolves(self) -> list[Player]:
        return [p for p in self.alive_players if p.role == Role.WEREWOLF]

    @property
    def alive_villagers(self) -> list[Player]:
        return [
            p
            for p in self.alive_players
            if p.role in (Role.VILLAGER, Role.SEER, Role.GUARD)
        ]

    @property
    def alive_seers(self) -> list[Player]:
        return [p for p in self.alive_players if p.role == Role.SEER]

    @property
    def alive_guards(self) -> list[Player]:
        return [p for p in self.alive_players if p.role == Role.GUARD]

    def check_winner(self) -> str | None:
        wolves = len(self.alive_wolves)
        villagers = len(self.alive_villagers)
        if wolves == 0:
            return "villagers"
        if wolves >= villagers:
            return "werewolves"
        return None
