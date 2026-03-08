from unittest.mock import AsyncMock, MagicMock

from app.narrator import GameSummary, Narrator


class TestGameSummary:
    def test_empty_render(self):
        gs = GameSummary()
        assert gs.render() == ""

    def test_record_game_start(self):
        gs = GameSummary()
        gs.record_game_start(["Alpha", "Beta", "Gamma"])
        rendered = gs.render()
        assert "3 players" in rendered
        assert "Alpha" in rendered

    def test_record_night_result_with_victim(self):
        gs = GameSummary()
        gs.record_night_result(1, "Alpha")
        assert "Alpha was found dead" in gs.render()

    def test_record_night_result_no_victim(self):
        gs = GameSummary()
        gs.record_night_result(1, None)
        assert "failed to claim" in gs.render()

    def test_record_vote_result_banished_wolf(self):
        gs = GameSummary()
        gs.record_vote_result(1, "Evil", was_wolf=True, had_runoff=False)
        rendered = gs.render()
        assert "Evil" in rendered
        assert "werewolf" in rendered

    def test_record_vote_result_no_banishment(self):
        gs = GameSummary()
        gs.record_vote_result(1, None, was_wolf=False, had_runoff=False)
        assert "No one was banished" in gs.render()

    def test_record_game_end(self):
        gs = GameSummary()
        gs.record_game_end("villagers")
        assert "villagers have won" in gs.render()

    def test_compaction_under_limit(self):
        gs = GameSummary()
        gs.record_game_start(["A"])
        # Should not compact with very short content
        rendered = gs.render()
        assert "Previously" not in rendered

    def test_discussion_highlights_empty(self):
        gs = GameSummary()
        gs.record_discussion_highlights(1, [])
        assert "uneasy silence" in gs.render()

    def test_discussion_highlights_with_entries(self):
        gs = GameSummary()
        entries = [
            {"team": "Alpha", "message": "I think Beta is suspicious"},
            {"team": "Beta", "message": "No way, it's Gamma"},
        ]
        gs.record_discussion_highlights(1, entries)
        rendered = gs.render()
        assert "Alpha" in rendered
        assert "2 messages" in rendered


class TestNarrator:
    async def test_no_api_key_returns_empty(self, override_settings):
        override_settings(openai_api_key="")
        narrator = Narrator()
        result = await narrator.narrate_game_start([])
        assert result == ""

    async def test_generate_with_mocked_client(self, override_settings):
        override_settings(openai_api_key="test-key")
        narrator = Narrator()

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "[narration]"

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        narrator._client = mock_client

        result = await narrator.narrate_game_start([])
        assert result == "[narration]"

    async def test_generate_handles_exception(self, override_settings):
        override_settings(openai_api_key="test-key")
        narrator = Narrator()

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=RuntimeError("API down")
        )
        narrator._client = mock_client

        result = await narrator.narrate_game_start([])
        assert result == ""
