import random
from collections import Counter
from dataclasses import dataclass, field

from app.models.game import GameState, Player


@dataclass
class NightVoteResult:
    victim: Player | None
    was_guarded: bool
    saved_player_id: str | None


@dataclass
class BanishmentTally:
    winner_id: str | None = None
    tied_ids: list[str] = field(default_factory=list)


def resolve_night_votes(
    state: GameState,
    guard_protected: str | None,
) -> NightVoteResult:
    """Tally wolf night votes and apply guard protection.

    If no votes were cast, a random villager is picked (wolves forfeited).
    Ties among top vote-getters are broken randomly.
    """
    victim: Player | None = None
    if not state.night_votes:
        targets = state.alive_villagers
        if targets:
            victim = random.choice(targets)
    else:
        vote_counts = Counter(state.night_votes.values())
        max_votes = max(vote_counts.values())
        top_targets = [t for t, c in vote_counts.items() if c == max_votes]
        target_id = random.choice(top_targets)
        victim = state.players.get(target_id)

    if victim and guard_protected and victim.id == guard_protected:
        return NightVoteResult(victim=None, was_guarded=True, saved_player_id=victim.id)

    return NightVoteResult(victim=victim, was_guarded=False, saved_player_id=None)


def tally_banishment_votes(votes: dict[str, str]) -> BanishmentTally:
    """Pure tally of banishment votes.

    winner_id is set iff a single target has the top vote count.
    tied_ids is set iff two or more targets tie for the top (runoff candidates).
    Both empty → no votes were cast.
    """
    if not votes:
        return BanishmentTally()

    vote_counts = Counter(votes.values())
    max_votes = max(vote_counts.values())
    tied = [t for t, c in vote_counts.items() if c == max_votes]

    if len(tied) == 1:
        return BanishmentTally(winner_id=tied[0])
    return BanishmentTally(tied_ids=tied)
