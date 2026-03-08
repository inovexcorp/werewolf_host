import logging

from openai import AsyncOpenAI

from app.config import settings
from app.models.game import PlayerInfo, Role

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are the dramatic host of a Werewolf game, inspired by reality TV shows \
like The Traitors. You narrate game events with an eloquent, theatrical flair — murders are \
grim discoveries, banishments are tense reveals, and every phase transition \
drips with suspense.

Keep narrations SHORT (3-4 sentences). Be vivid, punchy, and darkly entertaining. \
Never reveal hidden information. Use the players' team names for personality.\
"""


class Narrator:
    """
    A class for generating narrative descriptions and immersive storytelling within a game context.

    Provides methods to asynchronously generate narratives for game scenarios, including the game's start,
    phases, events like murders or banishments, and the game's conclusion. The class is integrated with
    the OpenAI API for generating dynamic and tailored text based on player roles, game states, and user-defined
    prompts.

    :ivar players: Represents the list of players within the game, containing their roles and teams.
    :type players: list[PlayerInfo]
    """

    def __init__(self):
        """
        Initializes a new instance of the class.

        This constructor method is designed to set up initial attributes
        necessary for the instance. The initialization includes private
        or internal settings that are essential for the proper functioning
        of the class.

        Attributes
        ----------
        None
        """
        self._client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI:
        """
        Retrieves or initializes the AsyncOpenAI client instance with appropriate configuration.

        If the client instance is not already initialized, this method creates a new instance
        of AsyncOpenAI using the configuration provided in the `settings` module. It checks
        for the presence of `openai_api_key` and/or `openai_base_url` in the settings and
        includes them in the initialization if available.

        :returns: An instance of AsyncOpenAI configured with the provided API key and base URL.
        :rtype: AsyncOpenAI
        """
        if self._client is None:
            kwargs = {}
            if settings.openai_api_key:
                kwargs["api_key"] = settings.openai_api_key
            if settings.openai_base_url:
                kwargs["base_url"] = settings.openai_base_url
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def _generate(self, prompt: str) -> str:
        """
        Generates a response using the provided prompt through an asynchronous interaction
        with the OpenAI API. This function internally initializes the client, sends the
        prompt along with system messages, and retrieves the generated completion.

        If the OpenAI API key is not set in the settings, it returns an empty string.
        Any failures during the process are logged, and an empty string is returned.

        :param prompt: The input prompt string based on which the function generates the
            response.
        :return: A string containing the generated content from the API, or an empty
            string if the generation fails.
        """
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
        """
        Generates a narration describing the start of the game based on the provided
        players, their teams, and the overall context of the game.

        :param players: The list of PlayerInfo objects, each containing details
            about a player's team and other attributes relevant to the game.
        :return: A string containing a narrative description of the game's start
            including the number of players and their respective teams.
        """
        names = ", ".join(p.team for p in players)
        return await self._generate(
            f"The game begins with {len(players)} players: {names}. "
            "Set the scene — night falls on the village. Wolves lurk among them."
        )

    async def narrate_phase(self, phase: str, round_num: int) -> str:
        """
        Generates a narrative description for the current phase of the game.

        This asynchronous method provides a narrative description based on
        the specified phase of the game and the round number. The narration
        is tailored to evoke appropriate mood and tension corresponding to
        the game phase, such as "night" or "discussion".

        :param phase: The current phase of the game. Expected values are
            "night" or "discussion".
        :type phase: str
        :param round_num: The current round number in the game. Represents
            an integer value greater than or equal to 1.
        :type round_num: int
        :return: A string containing the generated narrative description for
            the current phase and round.
        :rtype: str
        """
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
        """
        Generates a narration for a murder scenario in a game setting.

        The method constructs a dramatic announcement of a victim's demise,
        including the designation of their team and their specific role. The
        resulting narration can be used in various contexts such as game
        interactions or story progressions.

        :param victim_team: The team of the victim who was murdered.
        :type victim_team: str
        :param victim_role: The specific role of the victim in the game.
        :type victim_role: Role
        :return: A dramatic narration of the victim's murder.
        :rtype: str
        """
        return await self._generate(
            f"The village wakes to discover that {victim_team} has been murdered. "
            f"They were a {victim_role.value}. Announce this dramatically."
        )

    async def narrate_banishment(self, player_team: str, player_role: Role) -> str:
        """
        Generates a narrative about the banishment of a player in the game, based on their
        team and role. The narrative will differ depending on whether the player was a
        werewolf or an innocent villager.

        :param player_team: The name or identifier of the team being banished.
        :type player_team: str
        :param player_role: The role of the player being banished, which determines the
                            narrative context.
        :type player_role: Role
        :return: A narrative string tailored to the role and context of the banished
                 player.
        :rtype: str
        """
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
        """
        Summarize and generate a dramatic conclusion to the game, including the outcome and the roles of specific players.

        :param winner: The winner of the game, typically "werewolves" or "villagers".
        :type winner: str
        :param final_roles: A dictionary mapping player IDs to their final roles in the game.
        :type final_roles: dict[str, str]
        :return: A formatted string narrating the outcome of the game and the roles of key participants.
        :rtype: str
        """
        wolves = [pid for pid, role in final_roles.items() if role == "werewolf"]
        return await self._generate(
            f"The game is over. The {winner} have won! "
            f"The werewolves were: {', '.join(wolves)}. "
            "Deliver a dramatic finale."
        )
