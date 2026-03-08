import time

from app.config import settings


class RateLimiter:
    def __init__(self):
        # In-memory tracking per game round — reset each discussion phase
        # Key: (game_id, agent_id), Value: {count, last_ts}
        self._counters: dict[tuple[str, str], dict] = {}

    def _key(self, game_id: str, agent_id: str) -> tuple[str, str]:
        return (game_id, agent_id)

    def reset_for_phase(self, game_id: str, agent_ids: list[str]):
        for aid in agent_ids:
            self._counters[self._key(game_id, aid)] = {
                "count": 0,
                "last_ts": 0.0,
            }

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

        if counter["count"] >= settings.max_messages_per_discussion:
            return "MESSAGE_LIMIT_REACHED"

        now = time.monotonic()
        if now - counter["last_ts"] < settings.message_cooldown_seconds:
            return "RATE_LIMITED"

        counter["count"] += 1
        counter["last_ts"] = now
        return None


rate_limiter = RateLimiter()
