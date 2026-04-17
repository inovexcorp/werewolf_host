import asyncio
import random
from typing import TYPE_CHECKING

from app.config import settings
from app.eliminations import eliminate_player
from app.models.game import EliminationMethod, Phase, Player, Role
from app.models.messages import (
    AgentBanishmentVote,
    AgentChatMessage,
    AgentTypingIndicator,
    ErrorMessage,
    PhaseChangeMessage,
    VoteResultMessage,
    VoteUpdateMessage,
)
from app.rate_limiter import rate_limiter

if TYPE_CHECKING:
    from app.engine import GameEngine

NARRATOR_ID = "narrator"


async def run_introduction_phase(engine: "GameEngine") -> None:
    engine.state.phase = Phase.INTRODUCTION

    rate_limiter.reset_for_phase(
        engine.state.game_id,
        engine.state.alive_player_ids,
        max_messages=settings.introduction_max_messages,
        cooldown_seconds=settings.introduction_cooldown_seconds,
    )

    narration = await engine.narrator.narrate_phase("introduction", 0)

    phase_msg = PhaseChangeMessage(
        phase=Phase.INTRODUCTION,
        round=0,
        time_remaining_seconds=settings.introduction_duration,
        alive_players=engine.state.alive_player_ids,
        host_narration=narration,
    )
    await engine.ws.broadcast(engine.state.alive_player_ids, phase_msg)
    await engine._publish("phase_change", {"phase": "introduction", "round": 0})

    kickoff = await engine.narrator.generate_introduction_kickoff()
    await engine.ws.broadcast_chat(engine.state.alive_player_ids, NARRATOR_ID, kickoff)
    await engine._publish(
        "chat_message",
        {
            "from": NARRATOR_ID,
            "message": kickoff,
        },
    )
    engine.state.chat_log.append(
        {
            "channel": "public",
            "from": NARRATOR_ID,
            "message": kickoff,
            "round": 0,
            "phase": "introduction",
        }
    )

    async def discussion_handler(aid, m):
        return await _handle_discussion_message(engine, aid, m)

    await engine._collect_messages_for(
        settings.introduction_duration,
        allowed_handler=discussion_handler,
    )

    intro_chats = [
        {
            "team": engine.state.players[entry["from"]].id,
            "message": entry["message"],
        }
        for entry in engine.state.chat_log
        if entry.get("round") == 0
        and entry.get("phase") == "introduction"
        and entry.get("channel") == "public"
        and entry.get("from") != NARRATOR_ID
    ]
    engine.narrator.summary.record_discussion_highlights(0, intro_chats)


async def run_night_phase(engine: "GameEngine") -> None:
    engine.state.phase = Phase.NIGHT
    engine.state.night_votes = {}
    engine._seer_inspected = False
    engine._guard_protected = None
    engine._guard_acted = False

    narration = await engine.narrator.narrate_phase("night", engine.state.round)

    phase_msg = PhaseChangeMessage(
        phase=Phase.NIGHT,
        round=engine.state.round,
        time_remaining_seconds=settings.night_duration,
        alive_players=engine.state.alive_player_ids,
        host_narration=narration,
    )
    await engine.ws.broadcast(engine.state.alive_player_ids, phase_msg)
    await engine._publish(
        "phase_change", {"phase": "night", "round": engine.state.round}
    )

    for guard in engine.state.alive_guards:
        guard_kickoff = await engine.narrator.generate_guard_kickoff(engine.state.round)
        await engine.ws.send(
            guard.id,
            PhaseChangeMessage(
                phase=Phase.NIGHT,
                round=engine.state.round,
                time_remaining_seconds=settings.night_duration,
                alive_players=engine.state.alive_player_ids,
                host_narration=guard_kickoff,
            ),
        )

    wolf_ids = [p.id for p in engine.state.alive_wolves]
    kickoff = await engine.narrator.generate_wolf_kickoff(engine.state.round)
    await engine.ws.broadcast_wolf_chat(wolf_ids, NARRATOR_ID, kickoff)
    await engine._publish(
        "wolf_chat_message",
        {
            "from": NARRATOR_ID,
            "message": kickoff,
            "round": engine.state.round,
        },
    )
    engine.state.chat_log.append(
        {
            "channel": "wolf",
            "from": NARRATOR_ID,
            "message": kickoff,
            "round": engine.state.round,
            "phase": "night",
        }
    )

    for seer in engine.state.alive_seers:
        seer_kickoff = await engine.narrator.generate_seer_kickoff(engine.state.round)
        await engine.ws.send(
            seer.id,
            PhaseChangeMessage(
                phase=Phase.NIGHT,
                round=engine.state.round,
                time_remaining_seconds=settings.night_duration,
                alive_players=engine.state.alive_player_ids,
                host_narration=seer_kickoff,
            ),
        )

    await engine._collect_messages_for(
        settings.night_duration,
        allowed_handler=engine._handle_night_message,
    )

    if not engine._guard_acted:
        for guard in engine.state.alive_guards:
            await engine._publish(
                "guard_sleep",
                {
                    "guard": guard.id,
                    "round": engine.state.round,
                },
            )

    if not engine._seer_inspected:
        for seer in engine.state.alive_seers:
            await engine._publish(
                "seer_sleep",
                {
                    "seer": seer.id,
                    "round": engine.state.round,
                },
            )


async def morning_announcement(
    engine: "GameEngine",
    victim: Player | None,
    was_guarded: bool = False,
    saved_player_id: str | None = None,
) -> None:
    engine.state.phase = Phase.MORNING

    if victim and victim.alive:
        narration = await engine.narrator.narrate_murder(victim.id, victim.role)
        await eliminate_player(
            engine.state,
            engine.ws,
            engine._publish,
            victim,
            EliminationMethod.MURDER,
            narration,
        )
        engine.narrator.summary.record_night_result(engine.state.round, victim.id)
    elif was_guarded:
        narration = await engine.narrator.narrate_guard_save()
        if narration:
            phase_msg = PhaseChangeMessage(
                phase=Phase.MORNING,
                round=engine.state.round,
                time_remaining_seconds=0,
                alive_players=engine.state.alive_player_ids,
                host_narration=narration,
            )
            await engine.ws.broadcast(engine.state.alive_player_ids, phase_msg)
        await engine._publish(
            "guard_save",
            {
                "round": engine.state.round,
                "narration": narration,
                "saved_player": saved_player_id,
            },
        )
        engine.narrator.summary.record_night_result(engine.state.round, None)
    else:
        narration = await engine.narrator.narrate_peaceful_night()
        if narration:
            phase_msg = PhaseChangeMessage(
                phase=Phase.MORNING,
                round=engine.state.round,
                time_remaining_seconds=0,
                alive_players=engine.state.alive_player_ids,
                host_narration=narration,
            )
            await engine.ws.broadcast(engine.state.alive_player_ids, phase_msg)
        engine.narrator.summary.record_night_result(engine.state.round, None)

    await asyncio.sleep(settings.morning_announcement_pause)


async def run_discussion_phase(engine: "GameEngine") -> None:
    engine.state.phase = Phase.DISCUSSION

    rate_limiter.reset_for_phase(engine.state.game_id, engine.state.alive_player_ids)

    narration = await engine.narrator.narrate_phase("discussion", engine.state.round)

    phase_msg = PhaseChangeMessage(
        phase=Phase.DISCUSSION,
        round=engine.state.round,
        time_remaining_seconds=settings.discussion_duration,
        alive_players=engine.state.alive_player_ids,
        host_narration=narration,
    )
    await engine.ws.broadcast(engine.state.alive_player_ids, phase_msg)
    await engine._publish(
        "phase_change", {"phase": "discussion", "round": engine.state.round}
    )

    kickoff = await engine.narrator.generate_discussion_kickoff(engine.state.round)
    await engine.ws.broadcast_chat(engine.state.alive_player_ids, NARRATOR_ID, kickoff)
    await engine._publish(
        "chat_message",
        {
            "from": NARRATOR_ID,
            "message": kickoff,
        },
    )
    engine.state.chat_log.append(
        {
            "channel": "public",
            "from": NARRATOR_ID,
            "message": kickoff,
            "round": engine.state.round,
            "phase": "discussion",
        }
    )

    async def discussion_handler(aid, m):
        return await _handle_discussion_message(engine, aid, m)

    await engine._collect_messages_for(
        settings.discussion_duration,
        allowed_handler=discussion_handler,
    )

    round_chats = [
        {
            "team": engine.state.players[entry["from"]].id,
            "message": entry["message"],
        }
        for entry in engine.state.chat_log
        if entry.get("round") == engine.state.round
        and entry.get("phase") == "discussion"
        and entry.get("channel") == "public"
        and entry.get("from") != NARRATOR_ID
    ]
    engine.narrator.summary.record_discussion_highlights(
        engine.state.round, round_chats
    )

    discussion_summary = await engine.narrator.generate_discussion_summary(round_chats)
    if discussion_summary:
        await engine._publish(
            "discussion_summary",
            {
                "round": engine.state.round,
                "summary": discussion_summary,
            },
        )


async def _handle_discussion_message(engine: "GameEngine", agent_id: str, msg) -> bool:
    player = engine.state.players.get(agent_id)
    if not player or not player.alive:
        return False

    if isinstance(msg, AgentChatMessage):
        error_code = rate_limiter.check_chat_message(
            engine.state.game_id, agent_id, msg.message
        )
        if error_code:
            await engine.ws.send(
                agent_id,
                ErrorMessage(code=error_code, message=f"Chat rejected: {error_code}"),
            )
            return False

        engine._fire_and_forget(
            engine.ws.broadcast_chat(
                engine.state.alive_player_ids, agent_id, msg.message
            )
        )
        engine.state.chat_log.append(
            {
                "channel": "public",
                "from": agent_id,
                "message": msg.message,
                "round": engine.state.round,
                "phase": engine.state.phase.value,
            }
        )
        engine._fire_and_forget(
            engine._publish(
                "chat_message",
                {
                    "from": agent_id,
                    "message": msg.message,
                },
            )
        )
        return True

    if isinstance(msg, AgentTypingIndicator):
        engine._fire_and_forget(
            engine.ws.broadcast_typing(
                [p for p in engine.state.alive_player_ids if p != agent_id],
                agent_id,
                msg.is_typing,
            )
        )
        engine._fire_and_forget(
            engine._publish(
                "typing_indicator",
                {
                    "agent_id": agent_id,
                    "is_typing": msg.is_typing,
                },
            )
        )
        return True

    return False


async def run_voting_phase(engine: "GameEngine") -> Player | None:
    engine._had_runoff = False
    engine._first_round_votes = {}
    return await _run_vote_round(engine, settings.voting_duration, is_runoff=False)


async def _run_vote_round(
    engine: "GameEngine",
    duration: int,
    is_runoff: bool,
    candidates: list[str] | None = None,
) -> Player | None:
    if is_runoff:
        engine._first_round_votes = dict(engine.state.banishment_votes)
    phase = Phase.RUNOFF_VOTING if is_runoff else Phase.VOTING
    engine.state.phase = phase
    engine.state.banishment_votes = {}

    phase_msg = PhaseChangeMessage(
        phase=phase,
        round=engine.state.round,
        time_remaining_seconds=duration,
        alive_players=engine.state.alive_player_ids,
        host_narration="The village must vote."
        if not is_runoff
        else "A runoff vote begins!",
    )
    await engine.ws.broadcast(engine.state.alive_player_ids, phase_msg)
    await engine._publish(
        "phase_change", {"phase": str(phase), "round": engine.state.round}
    )

    total_voters = len(engine.state.alive_player_ids)

    async def vote_handler(agent_id: str, msg) -> bool:
        player = engine.state.players.get(agent_id)
        if not player or not player.alive:
            return False

        if isinstance(msg, AgentBanishmentVote):
            target = msg.target
            valid_targets = candidates or [
                p for p in engine.state.alive_player_ids if p != agent_id
            ]
            if target not in valid_targets:
                await engine.ws.send(
                    agent_id,
                    ErrorMessage(code="INVALID_TARGET", message="Invalid vote target."),
                )
                return False
            engine.state.banishment_votes[agent_id] = target

            vote_update = VoteUpdateMessage(
                votes_cast=len(engine.state.banishment_votes),
                votes_total=total_voters,
                time_remaining_seconds=0,
            )
            engine._fire_and_forget(
                engine.ws.broadcast(engine.state.alive_player_ids, vote_update)
            )
            return True

        return False

    await engine._collect_messages_for(duration, allowed_handler=vote_handler)

    for pid in engine.state.alive_player_ids:
        if pid not in engine.state.banishment_votes:
            valid = candidates or [p for p in engine.state.alive_player_ids if p != pid]
            if valid:
                engine.state.banishment_votes[pid] = random.choice(valid)

    return await engine._resolve_banishment_votes(is_runoff, candidates)


async def banishment_reveal(engine: "GameEngine", banished: Player | None) -> None:
    engine.state.phase = Phase.BANISHMENT

    if banished and banished.alive:
        narration = await engine.narrator.narrate_banishment(banished.id, banished.role)
        await eliminate_player(
            engine.state,
            engine.ws,
            engine._publish,
            banished,
            EliminationMethod.BANISHMENT,
            narration,
        )
        engine.narrator.summary.record_vote_result(
            engine.state.round,
            banished.id,
            was_wolf=banished.role == Role.WEREWOLF,
            had_runoff=engine._had_runoff,
        )
    else:
        engine.narrator.summary.record_vote_result(
            engine.state.round, None, was_wolf=False, had_runoff=False
        )

    vote_narration = await engine.narrator.narrate_vote_summary(
        round_num=engine.state.round,
        final_votes=engine.state.banishment_votes,
        banished_team=banished.id if banished else None,
        had_runoff=engine._had_runoff,
        first_round_votes=(
            engine._first_round_votes if engine._first_round_votes else None
        ),
    )

    first_round = engine._first_round_votes if engine._first_round_votes else None

    vote_result_msg = VoteResultMessage(
        round=engine.state.round,
        votes=engine.state.banishment_votes,
        had_runoff=engine._had_runoff,
        first_round_votes=first_round,
        banished_team=banished.id if banished else None,
        banished_role=banished.role.value if banished and banished.role else None,
        host_narration=vote_narration,
    )
    await engine.ws.broadcast(engine.state.alive_player_ids, vote_result_msg)
    if banished:
        await engine.ws.send(banished.id, vote_result_msg)

    await engine._publish(
        "vote_summary",
        {
            "round": engine.state.round,
            "narration": vote_narration,
            "had_runoff": engine._had_runoff,
            "first_round_votes": first_round,
            "final_votes": engine.state.banishment_votes,
            "banished": (
                {
                    "team": banished.id,
                    "role": banished.role.value if banished.role else "unknown",
                }
                if banished
                else None
            ),
            "roster": _player_roster(engine),
        },
    )

    await asyncio.sleep(settings.banishment_reveal_pause)


def _player_roster(engine: "GameEngine") -> list[dict]:
    return [
        {
            "id": p.id,
            "team": p.team,
            "role": p.role.value if p.role else "unknown",
            "alive": p.alive,
            "avatar_url": p.avatar_url,
        }
        for p in engine.state.players.values()
    ]
