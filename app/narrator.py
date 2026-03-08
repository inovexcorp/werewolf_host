import logging

from openai import AsyncOpenAI

from app.config import settings
from app.models.game import PlayerInfo, Role

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are the dramatic host of a Werewolf game, inspired by reality TV shows \
like The Traitors. You narrate game events with theatrical flair — murders are \
grim discoveries, banishments are tense reveals, and every phase transition \
drips with suspense.

Keep narrations SHORT (3-5 sentences). Be vivid, punchy, and darkly entertaining. \
Never reveal hidden information. Use the players' team names for personality.\
"""


class Narrator:
    def __init__(self):
        self._client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            kwargs = {}
            if settings.openai_api_key:
                kwargs["api_key"] = settings.openai_api_key
            if settings.openai_base_url:
                kwargs["base_url"] = settings.openai_base_url
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def _generate(self, prompt: str) -> str:
        if not settings.openai_api_key:
            return ""
        try:
            client = self._get_client()
            response = await client.chat.completions.create(
                model=settings.narrator_model,
                max_tokens=200,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
            )
            return response.choices[0].message.content or ""
        except Exception:
            logger.exception("Narrator generation failed")
            return ""

    async def narrate_game_start(self, players: list[PlayerInfo]) -> str:
        names = ", ".join(p.team for p in players)
        return await self._generate(
            f"The game begins with {len(players)} players: {names}. "
            "Set the scene — night falls on the village. Wolves lurk among them."
        )

    async def narrate_phase(self, phase: str, round_num: int) -> str:
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
        return await self._generate(
            f"The village wakes to discover that {victim_team} has been murdered. "
            f"They were a {victim_role.value}. Announce this dramatically."
        )

    async def narrate_banishment(self, player_team: str, player_role: Role) -> str:
        was_wolf = player_role == Role.WEREWOLF
        if was_wolf:
            return await self._generate(
                f"The village voted to banish {player_team}. "
                f"The reveal: they WERE a werewolf! The village got one right. "
                "Make this a triumphant moment."
            )
        return await self._generate(
            f"The village voted to banish {player_team}. "
            f"The reveal: they were an innocent villager. "
            "Make this a tragic, gut-wrenching moment — the wolves are still out there."
        )

    async def narrate_game_end(
        self, winner: str, final_roles: dict[str, str]
    ) -> str:
        wolves = [pid for pid, role in final_roles.items() if role == "werewolf"]
        return await self._generate(
            f"The game is over. The {winner} have won! "
            f"The werewolves were: {', '.join(wolves)}. "
            "Deliver a dramatic finale."
        )
