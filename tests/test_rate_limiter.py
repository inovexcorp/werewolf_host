import time_machine

from app.rate_limiter import RateLimiter


class TestRateLimiter:
    def setup_method(self):
        self.rl = RateLimiter()

    def test_allowed_after_reset(self):
        self.rl.reset_for_phase("g1", ["a1"])
        result = self.rl.check_chat_message("g1", "a1", "hello")
        assert result is None

    def test_message_too_long(self, override_settings):
        override_settings(max_message_length=10)
        self.rl.reset_for_phase("g1", ["a1"])
        result = self.rl.check_chat_message("g1", "a1", "x" * 11)
        assert result == "MESSAGE_TOO_LONG"

    def test_not_in_discussion(self):
        result = self.rl.check_chat_message("g1", "unknown", "hi")
        assert result == "NOT_IN_DISCUSSION"

    def test_message_limit_reached(self, override_settings):
        override_settings(
            max_messages_per_discussion=2,
            message_cooldown_seconds=0.0,
        )
        self.rl.reset_for_phase("g1", ["a1"])
        assert self.rl.check_chat_message("g1", "a1", "m1") is None
        assert self.rl.check_chat_message("g1", "a1", "m2") is None
        assert self.rl.check_chat_message("g1", "a1", "m3") == "MESSAGE_LIMIT_REACHED"

    @time_machine.travel(0, tick=False)
    def test_rate_limited_within_cooldown(self, override_settings):
        override_settings(message_cooldown_seconds=3.0)
        self.rl.reset_for_phase("g1", ["a1"])
        assert self.rl.check_chat_message("g1", "a1", "m1") is None
        # Still at t=0 → within cooldown
        assert self.rl.check_chat_message("g1", "a1", "m2") == "RATE_LIMITED"

    def test_clear_for_game_removes_only_that_games_entries(self):
        self.rl.reset_for_phase("g1", ["a1", "a2"])
        self.rl.reset_for_phase("g2", ["a1"])
        assert len(self.rl._counters) == 3

        self.rl.clear_for_game("g1")
        assert len(self.rl._counters) == 1
        assert self.rl.check_chat_message("g1", "a1", "hi") == "NOT_IN_DISCUSSION"
        assert self.rl.check_chat_message("g2", "a1", "hi") is None

    def test_clear_for_game_missing_id_is_noop(self):
        self.rl.reset_for_phase("g1", ["a1"])
        self.rl.clear_for_game("nonexistent")
        assert self.rl.check_chat_message("g1", "a1", "hi") is None

    def test_cooldown_expires(self, override_settings, monkeypatch):
        override_settings(message_cooldown_seconds=3.0)
        import time

        call_count = 0

        def fake_monotonic():
            nonlocal call_count
            call_count += 1
            # First call (first message): t=100 (well past last_ts=0.0)
            # Second call (second message): t=104 (>3s after first)
            return 100.0 if call_count == 1 else 104.0

        monkeypatch.setattr(time, "monotonic", fake_monotonic)
        self.rl.reset_for_phase("g1", ["a1"])
        assert self.rl.check_chat_message("g1", "a1", "m1") is None
        assert self.rl.check_chat_message("g1", "a1", "m2") is None
