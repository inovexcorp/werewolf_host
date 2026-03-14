import logging

from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
)

from app.config import settings
from app.models.game import PlayerInfo, Role

logger = logging.getLogger(__name__)

MAX_SUMMARY_CHARS = 2000


class GameSummary:
    """Rolling markdown summary of game events for narrator context."""

    def __init__(self) -> None:
        self._sections: list[str] = []  # ordered markdown sections

    def record_game_start(self, player_teams: list[str]) -> None:
        """Record initial player roster (teams only, no roles)."""
        names = ", ".join(player_teams)
        self._sections.append(
            f"**Game Start:** {len(player_teams)} players entered the village: {names}."
        )

    def record_night_result(self, round_num: int, victim_team: str | None) -> None:
        """Record who was killed at night (role revealed post-death is safe)."""
        if victim_team:
            self._sections.append(
                f"**Round {round_num} — Night:** The wolves struck. "
                f"{victim_team} was found dead."
            )
        else:
            self._sections.append(
                f"**Round {round_num} — Night:** The wolves failed to claim a victim."
            )

    def record_discussion_highlights(
        self, round_num: int, chat_entries: list[dict]
    ) -> None:
        """Summarize key discussion messages — attribute by team name.

        Expects dicts with keys: "team", "message".
        Only public channel messages; wolf chat must never be included.
        """
        if not chat_entries:
            self._sections.append(
                f"**Round {round_num} — Discussion:** "
                "The village sat in uneasy silence."
            )
            return

        # Pick up to 5 representative messages
        samples: list[str] = []
        if len(chat_entries) <= 5:
            selected = chat_entries
        else:
            # first, last, and 3 evenly spaced from the middle
            indices = [0, len(chat_entries) - 1]
            step = (len(chat_entries) - 1) / 4
            indices += [int(step * i) for i in range(1, 4)]
            indices = sorted(set(indices))
            selected = [chat_entries[i] for i in indices]

        for entry in selected:
            # Truncate long messages for summary
            text = entry["message"][:120]
            if len(entry["message"]) > 120:
                text += "..."
            samples.append(f'  - **{entry["team"]}**: "{text}"')

        body = "\n".join(samples)
        self._sections.append(
            f"**Round {round_num} — Discussion** "
            f"({len(chat_entries)} messages):\n{body}"
        )

    def record_vote_result(
        self,
        round_num: int,
        banished_team: str | None,
        was_wolf: bool,
        had_runoff: bool,
    ) -> None:
        """Record banishment outcome. Role reveal is safe since player is now dead."""
        if not banished_team:
            self._sections.append(f"**Round {round_num} — Vote:** No one was banished.")
            return

        runoff_note = " (after a runoff)" if had_runoff else ""
        role_reveal = "a werewolf" if was_wolf else "an innocent villager"
        self._sections.append(
            f"**Round {round_num} — Vote:** The village banished "
            f"{banished_team}{runoff_note}. They were {role_reveal}!"
        )

    def record_game_end(self, winner: str) -> None:
        """Record final outcome."""
        self._sections.append(f"**Game Over:** The {winner} have won!")

    def render(self) -> str:
        """Return the full markdown summary for injection into narrator prompts.

        If the total exceeds MAX_SUMMARY_CHARS, compact older rounds into a
        brief 'previously' summary, keeping the most recent 2 rounds in full.
        """
        if not self._sections:
            return ""

        full = "\n\n".join(self._sections)
        if len(full) <= MAX_SUMMARY_CHARS:
            return full

        # Keep game-start (index 0) and compact older rounds
        # Find sections for the most recent 2 rounds by scanning from the end
        recent: list[str] = []
        older: list[str] = []
        rounds_seen = 0
        last_round_marker = ""

        for section in reversed(self._sections):
            # Detect round boundaries by "Round N" markers
            if section.startswith("**Round"):
                marker = section.split("—")[0].strip()
                if marker != last_round_marker:
                    rounds_seen += 1
                    last_round_marker = marker
            if rounds_seen <= 2:
                recent.insert(0, section)
            else:
                older.insert(0, section)

        if older:
            # Compact older sections into a brief summary
            compact = "**Previously:** " + " ".join(
                s.split(":", 1)[1].strip() if ":" in s else s for s in older
            )
            # Truncate compact if still too long
            if len(compact) > 600:
                compact = compact[:597] + "..."
            parts = [compact, *recent]
        else:
            parts = recent

        return "\n\n".join(parts)


SYSTEM_PROMPT = """\
You are the dramatic host of a Werewolf game, inspired by reality TV shows \
like The Traitors. You narrate game events with eloquent, theatrical \
flair — murders are grim discoveries, banishments are tense reveals, \
and every phase transition \
drips with suspense.

Keep narrations SHORT (3-4 sentences). Be vivid, punchy, and darkly entertaining. \
Never reveal hidden information. Use the players' team names for personality.\
"""


class Narrator:
    """Generate narrative descriptions for game events via an LLM.

    Provides async methods for narrating game start, phase transitions,
    murders, banishments, and the game conclusion. Integrated with the
    OpenAI API for dynamic text generation based on player roles and
    game state.
    """

    def __init__(self, host_backstory: str = ""):
        self._client: AsyncOpenAI | None = None
        self._host_backstory = host_backstory
        self.summary = GameSummary()

    def _get_client(self) -> AsyncOpenAI:
        """Retrieve or initialize the AsyncOpenAI client."""
        if self._client is None:
            kwargs = {}
            if settings.openai_api_key:
                kwargs["api_key"] = settings.openai_api_key
            if settings.openai_base_url:
                kwargs["base_url"] = settings.openai_base_url
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def _generate(self, prompt: str, max_tokens: int = 400) -> str:
        """Generate a response from the LLM. Returns "" on failure."""
        if not settings.openai_api_key:
            return ""
        try:
            client = self._get_client()
            system_content = SYSTEM_PROMPT
            if self._host_backstory:
                system_content += f"\n\nYour backstory:\n{self._host_backstory}"
            summary_text = self.summary.render()
            if summary_text:
                system_content += f"\n\n## Story so far:\n{summary_text}"
            response = await client.chat.completions.create(
                model=settings.narrator_model,
                max_tokens=max_tokens,
                messages=[
                    ChatCompletionSystemMessageParam(
                        role="system", content=system_content
                    ),
                    ChatCompletionUserMessageParam(role="user", content=prompt),
                ],
            )
            return response.choices[0].message.content or ""
        except Exception:
            logger.exception("Narrator generation failed")
            return ""

    async def generate_host_backstory(self) -> str:
        """Generate a short backstory for the host character at startup."""
        return await self._generate(
            "You are about to host a new series of Werewolf games. "
            "In 2-3 sentences, introduce yourself as the mysterious "
            "host of this deadly game. What is your name? What is "
            "your dark, humorous origin story? "
            "Make it fun and theatrical.",
            max_tokens=600,
        )

    async def generate_wolf_kickoff(self, round_num: int) -> str:
        """Generate a 1-2 sentence conspiratorial prompt for wolves to strategize."""
        text = await self._generate(
            f"Round {round_num}: The wolves are awake in their private channel. "
            "In 1-2 sentences, urge the wolves to coordinate and choose a target. "
            "Be conspiratorial and dramatic.",
            max_tokens=150,
        )
        return (
            text
            or "The wolves are awake. This is your private channel — strategize. "
            "Who should not see another dawn?"
        )

    async def generate_seer_kickoff(self, round_num: int) -> str:
        """Generate a private prompt for the Seer to choose an inspection target."""
        text = await self._generate(
            f"Round {round_num}: The Seer awakens in the night. "
            "In 1-2 sentences, urge the Seer to choose wisely — "
            "who do they wish to peer into the soul of tonight? "
            "Be mystical and dramatic.",
            max_tokens=150,
        )
        return (
            text
            or "The Seer awakens. Choose wisely — "
            "who do you wish to peer into the soul of tonight?"
        )

    async def generate_introduction_kickoff(self) -> str:
        """Generate a prompt encouraging players to introduce themselves."""
        text = await self._generate(
            "The players have just arrived in the village before the first night. "
            "In 1-2 sentences, warmly encourage them to introduce themselves and "
            "get to know one another. This is a friendly meet-and-greet — no "
            "accusations, no voting, just introductions. Be theatrical but welcoming.",
            max_tokens=150,
        )
        return (
            text
            or "Welcome, villagers! Before the darkness falls, take a moment to "
            "introduce yourselves. Who are you? What brings you to this village?"
        )

    async def generate_discussion_kickoff(self, round_num: int) -> str:
        """Generate a 1-2 sentence urgent prompt for village discussion."""
        text = await self._generate(
            f"Round {round_num}: The village gathers for discussion. "
            "In 1-2 sentences, urge the villagers to speak up and find the wolves. "
            "Be dramatic and urgent.\n\n"
            "CRITICAL RULES — you MUST follow these:\n"
            "- NEVER reveal who the werewolves are or hint at their identities\n"
            "- NEVER reference what happened in the wolves' private chat\n"
            "- NEVER reveal night vote targets or who voted for whom\n"
            "- Only reference publicly known information: who was killed, "
            "who was banished, and what was said in public discussion",
            max_tokens=150,
        )
        return (
            text
            or "The village gathers. Someone among you is a wolf. "
            "Speak now — or forever hold your peace."
        )

    async def narrate_game_start(self, players: list[PlayerInfo]) -> str:
        """Narrate the beginning of a new game."""
        names = ", ".join(p.team for p in players)
        return await self._generate(
            f"The game begins with {len(players)} players: {names}. "
            "Set the scene — night falls on the village. Wolves lurk among them."
        )

    async def narrate_phase(self, phase: str, round_num: int) -> str:
        """Narrate a phase transition (introduction, night, or discussion)."""
        if phase == "introduction":
            return await self._generate(
                "The players have gathered in the village square for the "
                "first time. Set the scene — a warm afternoon, new faces, "
                "the calm before the storm. No one suspects the danger yet."
            )
        if phase == "night":
            return await self._generate(
                f"Round {round_num}: Night falls. The wolves must choose their victim. "
                "Set the mood — darkness, danger, suspicion."
            )
        if phase == "discussion":
            return await self._generate(
                f"Round {round_num}: The roundtable discussion begins. "
                "The village must find the wolves among them. Build tension."
            )
        return ""

    async def narrate_murder(self, victim_team: str, victim_role: Role) -> str:
        """Narrate a wolf-kill murder discovery."""
        return await self._generate(
            f"The village wakes to discover that {victim_team} has been murdered. "
            f"They were a {victim_role.value}. Announce this dramatically."
        )

    async def narrate_banishment(self, player_team: str, player_role: Role) -> str:
        """Narrate a player banishment and role reveal."""
        if player_role == Role.WEREWOLF:
            return await self._generate(
                f"The village voted to banish {player_team}. "
                f"The reveal: they WERE a werewolf! The village got one right. "
                "Make this a triumphant moment."
            )
        if player_role == Role.SEER:
            return await self._generate(
                f"The village voted to banish {player_team}. "
                f"The reveal: they were the village Seer — a devastating loss. "
                "The one who could see the truth is gone. "
                "Make this a tragic, gut-wrenching moment."
            )
        return await self._generate(
            f"The village voted to banish {player_team}. "
            f"The reveal: they were an innocent villager. "
            "Make this a tragic, gut-wrenching moment — the wolves are still out there."
        )

    async def narrate_vote_summary(
        self,
        round_num: int,
        final_votes: dict[str, str],
        banished_team: str | None,
        had_runoff: bool,
        first_round_votes: dict[str, str] | None = None,
    ) -> str:
        """Narrate a dramatic vote summary for all players."""
        lines: list[str] = [f"Round {round_num} vote results:"]

        if had_runoff and first_round_votes:
            lines.append("First round votes:")
            for voter, target in first_round_votes.items():
                lines.append(f"  {voter} → {target}")

        lines.append("Final votes:" if had_runoff else "Votes:")
        for voter, target in final_votes.items():
            lines.append(f"  {voter} → {target}")

        if banished_team:
            lines.append(f"Result: {banished_team} was banished.")
        else:
            lines.append("Result: No one was banished.")

        lines.append(
            "Narrate this vote dramatically. Reference specific votes and alliances. "
            "4-6 sentences. Do NOT reveal anyone's role or identity as wolf/villager."
        )

        return await self._generate("\n".join(lines), max_tokens=600)

    async def narrate_game_end(self, winner: str, final_roles: dict[str, str]) -> str:
        """Narrate the dramatic game conclusion."""
        wolves = [pid for pid, role in final_roles.items() if role == "werewolf"]
        return await self._generate(
            f"The game is over. The {winner} have won! "
            f"The werewolves were: {', '.join(wolves)}. "
            "Deliver a dramatic finale."
        )
