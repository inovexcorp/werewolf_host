from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "WW_"}

    redis_url: str = "redis://localhost:6379/0"
    openai_api_key: str = ""
    openai_base_url: str = "https://litellm.inovexcorp.com/v1"
    narrator_model: str = "gpt-5-mini"

    # Phase timing (seconds)
    night_duration: int = 45
    discussion_duration: int = 90
    voting_duration: int = 30
    runoff_voting_duration: int = 30
    morning_announcement_pause: int = 5
    banishment_reveal_pause: int = 5

    # Rate limits
    max_messages_per_discussion: int = 5
    message_cooldown_seconds: float = 3.0
    max_message_length: int = 280
    typing_indicator_ttl: int = 5

    # Connection
    agent_response_timeout: int = 10
    reconnect_timeout: int = 10

    # Spectator
    spectator_secret: str = ""  # shared secret for spectator feed; empty = open access
    spectator_wolf_chat_delay: int = 0  # 0 = hidden until game end

    # Scoring
    scoring_wolf_win_points: int = 3  # points per alive wolf on wolf win
    scoring_villager_win_points: int = 1  # per alive villager (x count)

    # Avatars
    avatar_max_size_px: int = 512
    avatar_max_upload_bytes: int = 2_097_152  # 2 MB
    avatar_dir: str = "static/avatars"


settings = Settings()


# Werewolf scaling table: players -> number of werewolves
WOLF_SCALING: dict[int, int] = {
    5: 1,
    6: 1,
    7: 1,
    8: 2,
    9: 2,
    10: 2,
}


def wolves_for_player_count(n: int) -> int:
    if n < 5:
        raise ValueError(f"Need at least 5 players, got {n}")
    if n > 10:
        return 3
    return WOLF_SCALING[n]
