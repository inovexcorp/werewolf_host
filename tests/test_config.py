import pytest

from app.config import Settings, wolves_for_player_count


class TestSettings:
    def test_defaults(self):
        s = Settings()
        assert s.redis_url == "redis://localhost:6379/0"
        assert s.night_duration == 45
        assert s.max_messages_per_discussion == 5
        assert s.message_cooldown_seconds == 3.0
        assert s.max_message_length == 280

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("WW_REDIS_URL", "redis://custom:1234/1")
        s = Settings()
        assert s.redis_url == "redis://custom:1234/1"


class TestWolvesForPlayerCount:
    @pytest.mark.parametrize(
        "players,expected",
        [(5, 1), (6, 1), (7, 2), (8, 2), (9, 2), (10, 3)],
    )
    def test_scaling_table(self, players, expected):
        assert wolves_for_player_count(players) == expected

    def test_above_10_returns_3(self):
        assert wolves_for_player_count(11) == 3
        assert wolves_for_player_count(20) == 3

    def test_below_5_raises(self):
        with pytest.raises(ValueError, match="at least 5"):
            wolves_for_player_count(4)
