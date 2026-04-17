import time

from app.config import settings


class RateLimiter:
    def __init__(self):
        # In-memory tracking per game round — reset each discussion phase
        # Key: (game_id, agent_id), Value: {count, last_ts, max_messages, cooldown}
        self._counters: dict[tuple[str, str], dict] = {}

    def _key(self, game_id: str, agent_id: str) -> tuple[str, str]:
        return (game_id, agent_id)

    def reset_for_phase(
        self,
        game_id: str,
        agent_ids: list[str],
        max_messages: int | None = None,
        cooldown_seconds: float | None = None,
    ):
        for aid in agent_ids:
            self._counters[self._key(game_id, aid)] = {
                "count": 0,
                "last_ts": 0.0,
                "max_messages": max_messages
                if max_messages is not None
                else settings.max_messages_per_discussion,
                "cooldown": cooldown_seconds
                if cooldown_seconds is not None
                else settings.message_cooldown_seconds,
            }

    def clear_for_game(self, game_id: str) -> None:
        """Drop all counters for a finished game to prevent unbounded growth."""
        stale = [key for key in self._counters if key[0] == game_id]
        for key in stale:
            del self._counters[key]

    def check_chat_message(
        self, game_id: str, agent_id: str, message: str
    ) -> str | None:
        """Returns an error code string if rejected, None if allowed."""
        if len(message) > settings.max_message_length:
            return "MESSAGE_TOO_LONG"

        key = self._key(game_id, agent_id)
        counter = self._counters.get(key)
        if counter is None:
            return "NOT_IN_DISCUSSION"

        max_messages = counter.get("max_messages", settings.max_messages_per_discussion)
        cooldown = counter.get("cooldown", settings.message_cooldown_seconds)

        if counter["count"] >= max_messages:
            return "MESSAGE_LIMIT_REACHED"

        now = time.monotonic()
        if now - counter["last_ts"] < cooldown:
            return "RATE_LIMITED"

        counter["count"] += 1
        counter["last_ts"] = now
        return None


rate_limiter = RateLimiter()
