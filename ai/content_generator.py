import json
import re
from pathlib import Path

from ai.base_ai_service import BaseAIService
from ai.providers.ollama_provider import OllamaProvider


LOCATION_PIN = "\U0001F4CD"

DEFAULT_VISUAL_COUNT = 14
MIN_VISUAL_COUNT = 2
MAX_VISUAL_COUNT = 30

MAX_DESTINATION_LENGTH = 48

DEFAULT_MUSIC_MOODS = ["cinematic", "chill"]

# Mood words the music provider can map onto licensed genre pages.
ALLOWED_MUSIC_MOODS = (
    "lofi",
    "chill",
    "dreamy",
    "cinematic",
    "ambient",
    "melodic",
    "tropical",
    "upbeat",
    "atmospheric",
    "modern",
    "electronic",
    "inspiring",
    "relaxing",
    "warm",
    "energetic",
    "epic",
    "smooth",
    "nostalgic",
)


class ContentGenerationError(RuntimeError):

    pass


def build_schema(visual_count):
    """
    JSON schema passed to Ollama's structured-output mode.

    The shape is grammar-enforced so a small local model cannot drift
    into narration, facts or commentary: there is no narration field
    at all, and the visuals array is locked to exactly visual_count
    search queries (one per footage segment of the timeline).
    """

    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "music_mood": {"type": "string"},
            "visuals": {
                "type": "array",
                "minItems": visual_count,
                "maxItems": visual_count,
                "items": {
                    "type": "object",
                    "properties": {
                        "search_query": {"type": "string"},
                    },
                    "required": ["search_query"],
                },
            },
        },
        "required": [
            "title",
            "summary",
            "music_mood",
            "visuals",
        ],
    }


class ContentGenerator(BaseAIService):

    """
    The Prompt stage of the pipeline, scoped to destinations.

    Given an optional destination (or nothing at all), the AI returns
    the structured content for one cinematic travel Short:

        title            : the destination itself, "New York City, USA".
                           It becomes the upload title AND the on-screen
                           caption ("📍 New York City, USA")
        summary          : one short sentence describing the place. The
                           destination is not repeated in it - the title
                           already names the place
        music_mood       : 1-3 mood words used to pick a licensed track
        visuals          : the stock-footage search queries for the
                           destination (each one names the city AND the
                           country)

    There is no narration, no script and no fact checking - the
    destination is the entire content of the video.
    """

    def __init__(self, config):

        super().__init__(config, "CONTENT")

        self.llm = OllamaProvider(config)

        self.channel_config = config["content"]

        self.generation_config = self.channel_config.get(
            "content_generation",
            {}
        )

    def format_bullets(self, values):

        if not isinstance(values, list):

            return ""

        return "\n".join(
            f"- {str(v).strip()}"
            for v in values
            if str(v).strip()
        )

    def visual_count(self):

        """
        How many footage segments the timeline has. Config-driven so
        the existing "x clips per visual search query" behaviour is
        unchanged; only the number of queries is read from config.
        """

        value = self.generation_config.get(
            "visual_count",
            DEFAULT_VISUAL_COUNT
        )

        try:

            value = int(value)

        except (TypeError, ValueError):

            value = DEFAULT_VISUAL_COUNT

        return max(
            MIN_VISUAL_COUNT,
            min(value, MAX_VISUAL_COUNT)
        )

    def build_prompt(self, instruction=None):

        c = self.generation_config

        channel = self.channel_config.get("channel", {})

        name = str(
            channel.get("name", "Your Next Location")
        )

        description = str(
            channel.get(
                "description",
                channel.get("channel_description", "")
            )
        )

        example_destinations = self.format_bullets(
            c.get("destination_examples", [])
        )

        visual_rules = self.format_bullets(
            c.get("visual_rules", [])
        )

        music_rules = self.format_bullets(
            c.get("music_rules", [])
        )

        creative_directions = self.format_bullets(
            c.get("creative_directions", [])
        )

        # Duration is config-driven from app.shorts.target_duration_seconds:
        # the number of footage segments (and how long each clip stays on
        # screen) derives from that single source of truth.
        shorts = self.config.get("app", {}).get("shorts", {})

        target_seconds = float(
            shorts.get("target_duration_seconds", 50)
        )

        target_seconds_str = str(
            int(target_seconds)
        )

        visual_count = self.visual_count()

        seconds_per_clip = (
            target_seconds / visual_count
            if visual_count
            else target_seconds
        )

        instruction = str(instruction or "").strip()

        if instruction:

            instruction_section = (
                "USER DESTINATION / INSTRUCTION (HIGHEST PRIORITY)\n"
                "The user supplied the following destination or direction. If "
                "it names a place, use that exact destination - do not "
                "translate it, rename it, or add another city. If it asks for "
                "something else, follow it as closely as possible while keeping "
                "every requirement below:\n"
                f"{instruction}\n"
                "If no usable destination is supplied, choose one yourself at "
                "random as described in STEP 1.\n\n"
            )

        else:

            instruction_section = (
                "USER DESTINATION / INSTRUCTION\n"
                "The user supplied nothing, so you MUST choose the destination "
                "yourself at random as described in STEP 1.\n\n"
            )

        head = (
            f'You are the creative director for "{name}", a short-form '
            "cinematic travel channel.\n\n"
            "THE CHANNEL\n"
            f"{description}\n\n"
            "WHAT ONE VIDEO IS (CRITICAL)\n"
            "- ONE destination, beautiful footage of it, and music that fits "
            "it. Nothing else.\n"
            "- There is NO narration, NO voice over, NO subtitles, NO facts, "
            "NO story, NO commentary and NO explanatory text anywhere in the "
            "video.\n"
            "- The only text in the whole video is the opening caption "
            f'"{LOCATION_PIN} City, Country". It types itself on from left to '
            "right, holds briefly, then retraces off from right to left, and it "
            "is gone within the first few seconds. Everything after that is "
            "footage and music.\n"
            f"- The video is built from {visual_count} footage segments of about "
            f"{seconds_per_clip:.1f} seconds each, which is why you must return "
            f"exactly {visual_count} visual search queries.\n\n"
            "STEP 1 - CHOOSE THE DESTINATION\n"
            "- A destination is a real city, island, coastline, mountain region "
            "or country that exists today.\n"
            "- If the user supplied a place, use it EXACTLY as given - same "
            "spelling, same city, same country. Do not replace it.\n"
            "- Otherwise draw ONE destination at random from anywhere in the "
            "world. Every region must be roughly equally likely: Europe, Asia, "
            "North America, South America, Africa, Oceania, the Middle East, the "
            "Caribbean, the Arctic. Never default to the same handful of famous "
            "cities.\n"
            "- Choose places that are visually stunning and filmed often enough "
            "that beautiful stock footage exists.\n"
            '- Write the destination as "City, Country" (or "Region, Country" '
            "when it is not a city), in English, at most 40 characters, with the "
            "country written the way English speakers normally write it (USA, UK, "
            "Italy, Japan...).\n"
            "- Example destinations (style reference only - never copy the list):\n"
            f"{example_destinations}\n"
            "- NEVER invent a place and never combine two real places into one "
            "name.\n\n"
            "STEP 2 - THE TITLE (IT IS THE DESTINATION)\n"
            f'- "title": the pin emoji \"{LOCATION_PIN}\", one space, then the '
            "destination exactly as \"City, Country\", same spelling and same "
            "country as chosen in STEP 1. Do not add a suffix, a dash, episode "
            f"numbers or any other words. Example: \"{LOCATION_PIN} Lisbon, "
            "Portugal\".\n"
            "- The title is shown on screen exactly as returned, so it must be "
            f"\"{LOCATION_PIN} City, Country\" and nothing else.\n\n"
        )

        body = (
            "STEP 3 - SUMMARY (upload description, never shown in the video)\n"
            '- "summary": ONE short sentence describing what the place looks '
            "and feels like (about 8-15 words). Do NOT begin it with the "
            "destination name and do not list it - the title already names "
            "the place, so the summary only describes it. Example for "
            'Lisbon, Portugal: "Sunlit yellow trams climbing tiled hillsides '
            'above the Tagus river". Correct capitalization, spacing and '
            "final punctuation. One sentence only - no lists, no facts, no "
            "statistics, no second sentence, no destination prefix.\n\n"
            "STEP 4 - MUSIC MOOD\n"
            f"{music_rules}\n"
            '- "music_mood": 1-3 lowercase words separated by spaces, chosen only '
            "from this list: " + ", ".join(ALLOWED_MUSIC_MOODS) + ".\n\n"
            f"STEP 5 - THE {visual_count} VISUAL SEARCH QUERIES\n"
            f'- "visuals": EXACTLY {visual_count} objects, each with exactly one '
            'field: {"search_query": "stock footage search phrase"}.\n'
            f"{visual_rules}\n"
            "- Every search_query MUST include the full destination name "
            "including the country (for example \"queenstown new zealand lake "
            "aerial\"), never the city alone - otherwise the search engine "
            "returns footage of wrong places with similar names.\n"
            "- The queries are used in order, back to back, to build the "
            f"{target_seconds_str}-second video, so together they must show a "
            "complete, varied picture of the destination.\n\n"
            f"{instruction_section}"
            "CREATIVE DIRECTION\n"
            f"{creative_directions}\n\n"
            "FINAL CHECK BEFORE ANSWERING\n"
            f"1. title is exactly \"{LOCATION_PIN} City, Country\" - the pin "
            "emoji, one space, then the destination, and it matches the user's "
            "destination exactly when one was given.\n"
            "2. summary is one short sentence about the place and does NOT "
            "start with the destination name.\n"
            f"3. visuals contains exactly {visual_count} search queries, each "
            "one a concrete, filmable shot that includes the full destination "
            "name with the country.\n"
            "4. music_mood contains 1-3 words from the allowed list and fits "
            "the destination.\n"
            "5. There is no narration, no script, no facts, no story, no "
            "commentary, no extra text and no extra JSON fields anywhere in "
            "the response.\n"
        )

        return head + body

    def generate(self, instruction=None):

        visual_count = self.visual_count()

        prompt = self.build_prompt(instruction)

        self.log(
            "Generating destination content "
            f"({visual_count} visual queries)..."
        )

        response = self.llm.generate(
            prompt,
            response_format=build_schema(visual_count)
        )

        content = self.parse_content(response)

        if content is None:

            raise ContentGenerationError(
                "The AI response could not be parsed into valid "
                "destination content."
            )

        content = self.validate_content(content)

        self.log(
            "Destination: " + content["title"]
        )

        self.log(
            "Content generated: " + content["title"]
        )

        return content

    @staticmethod
    def _clean_text(value):

        """Light text hygiene: single-line, no surrounding quotes."""

        text = str(value or "").strip()

        text = re.sub(r"\s+", " ", text)

        return text.strip(" \t\"'`")

    def _format_destination(self, value):

        """
        Normalizes the destination into a clean "City, Country" line.

        The caption must always be one short horizontal line, so
        trailing periods, doubled commas and over-long values are
        trimmed here instead of rendering something unusable.
        """

        destination = self._clean_text(value)

        destination = destination.rstrip(".!,;:")

        destination = re.sub(r"\s*,\s*", ", ", destination)

        destination = re.sub(r",\s*,", ", ", destination)

        destination = re.sub(r"\s+", " ", destination).strip()

        if len(destination) > MAX_DESTINATION_LENGTH:

            destination = destination[:MAX_DESTINATION_LENGTH].rstrip()

            if " " in destination:

                destination = destination.rsplit(" ", 1)[0]

            destination = destination.rstrip(", ")

        return destination

    @staticmethod
    def location_caption(destination):

        """
        Returns the on-screen caption for a destination. Idempotent:
        a destination that already carries the pin is returned as-is,
        so the emoji can never be doubled.
        """

        text = str(destination or "").strip()

        if text.startswith(LOCATION_PIN):

            return text

        return f"{LOCATION_PIN} {text}".strip()

    @staticmethod
    def _strip_location_prefix(summary, destination):

        """
        Removes a leading location prefix from a summary.

        The summary describes the place, it does not name it - the
        title already carries the destination. A model (or content
        written before this rule existed) may still lead with
        "City, Country - ", so the prefix and its separator are
        removed and the remaining sentence is re-capitalized.
        """

        text = str(summary or "").strip()

        if not text or not destination:

            return text

        # Tolerate a pin emoji if the model echoed the title format.
        if text.startswith(LOCATION_PIN):

            text = text[len(LOCATION_PIN):].strip()

        if not text.lower().startswith(destination.lower()):

            return text

        remainder = text[len(destination):].lstrip()

        # Only a separator makes it a prefix, so a genuine sentence
        # that happens to begin with the destination name is left
        # alone.
        for separator in ("-", "\u2013", "\u2014", ":", "|", "\u00b7", ","):

            if remainder.startswith(separator):

                remainder = remainder[len(separator):].strip()

                # The summary was nothing but the location, so there
                # is no description left to keep.
                if not remainder:

                    return ""

                return remainder[:1].upper() + remainder[1:]

        return text

    def _ensure_destination_in_query(self, query, destination):

        """
        Makes sure a footage search query names the full destination,
        including the country, so the provider cannot return footage
        of a same-named place in the wrong country.

        Missing city -> the full destination is prepended.
        Missing country -> the country is appended.
        """

        query = self._clean_text(query)

        if not query or not destination:

            return query

        dest_tokens = set(
            re.findall(r"[a-z0-9]+", destination.lower())
        )

        query_tokens = set(
            re.findall(r"[a-z0-9]+", query.lower())
        )

        if dest_tokens and dest_tokens.issubset(query_tokens):

            return query

        parts = [
            part.strip()
            for part in destination.split(",")
            if part.strip()
        ]

        city = parts[0] if parts else destination

        country = parts[1] if len(parts) > 1 else ""

        city_tokens = set(
            re.findall(r"[a-z0-9]+", city.lower())
        )

        if not city_tokens & query_tokens:

            # The city is missing entirely - lead with the
            # full destination.

            return self._clean_text(
                f"{destination} {query}"
            )

        country_tokens = set(
            re.findall(r"[a-z0-9]+", country.lower())
        )

        if country_tokens and not country_tokens & query_tokens:

            return self._clean_text(
                f"{query} {country}"
            )

        return query

    def _normalize_mood(self, value):

        """
        Turns the model's music_mood string into 1-3 mood words the
        music provider understands. Unknown words are dropped so the
        provider never has to guess at an unmapped mood.
        """

        words = re.findall(
            r"[a-z]+",
            str(value or "").lower()
        )

        moods = []

        for word in words:

            if word in ALLOWED_MUSIC_MOODS and word not in moods:

                moods.append(word)

        return moods[:3]

    def parse_content(self, response):

        if not response:

            return None

        cleaned = response.strip()

        if "```" in cleaned:

            cleaned = re.sub(
                r"```(?:json)?",
                "",
                cleaned,
                flags=re.IGNORECASE
            )

            cleaned = cleaned.replace("```", "").strip()

        try:

            data = json.loads(cleaned)

        except (json.JSONDecodeError, TypeError):

            data = self._extract_json(cleaned)

            if data is None:

                return None

        if not isinstance(data, dict):

            return None

        # The title IS the destination. Older model responses may
        # still carry a separate destination field - fall back to it.
        title = self._clean_text(
            data.get("title", "")
        )

        if not title:

            title = self._format_destination(
                data.get("destination", "")
            )

        visuals = data.get("visuals", [])

        if not isinstance(visuals, list):

            visuals = []

        cleaned_visuals = []

        for visual in visuals:

            if not isinstance(visual, dict):

                continue

            search_query = self._clean_text(
                visual.get("search_query", "")
            )

            if not search_query:

                continue

            cleaned_visuals.append(
                {"search_query": search_query}
            )

        return {
            "title": title,
            "summary": self._clean_text(data.get("summary", "")),
            "music_mood": self._clean_text(data.get("music_mood", "")),
            "visuals": cleaned_visuals,
        }

    def _extract_json(self, text):

        match = re.search(
            r"\{.*\}",
            text,
            flags=re.DOTALL
        )

        if not match:

            return None

        try:

            return json.loads(match.group(0))

        except (json.JSONDecodeError, TypeError):

            return None

    def validate_content(self, content):

        content = (
            content
            if isinstance(content, dict)
            else {}
        )

        # The title is the destination - there is no separate
        # destination field anymore. Legacy content that still
        # carries one is accepted as a fallback.
        title = self._clean_text(content.get("title", ""))

        if not title:

            title = self._format_destination(
                content.get("destination", "")
            )

        # Strip any pin emoji the model already added so the
        # destination can be normalized cleanly, then re-add it
        # in the exact "📍 City, Country" format.
        bare_title = title.lstrip(LOCATION_PIN).strip()

        bare_title = self._format_destination(bare_title)

        if not bare_title:

            raise ContentGenerationError(
                "The AI did not return a destination."
            )

        # Strip a legacy "| ..." suffix so the title stays the bare
        # destination.
        bare_title = bare_title.split("|", 1)[0].strip()

        # The title always carries the pin: "📍 City, Country".
        title = self.location_caption(bare_title)

        summary = self._clean_text(content.get("summary", ""))

        # The summary describes the place, it does not name it - the
        # title already carries the destination, so any leading
        # location prefix is removed rather than added.
        summary = self._strip_location_prefix(summary, bare_title)

        if not summary:

            summary = "Cinematic travel moments from this destination."

        visuals = content.get("visuals", [])

        if not isinstance(visuals, list):

            visuals = []

        cleaned_visuals = []

        for visual in visuals:

            if not isinstance(visual, dict):

                continue

            search_query = self._clean_text(
                visual.get("search_query", "")
            )

            if not search_query:

                continue

            cleaned_visuals.append(
                {
                    "search_query": self._ensure_destination_in_query(
                        search_query,
                        bare_title
                    )
                }
            )

        if not cleaned_visuals:

            raise ContentGenerationError(
                "The AI returned no usable visual search queries."
            )

        requested = self.visual_count()

        if len(cleaned_visuals) != requested:

            self.log(
                f"Note: {len(cleaned_visuals)} visual queries returned "
                f"(requested {requested}). Proceeding with what was "
                "returned."
            )

        moods = self._normalize_mood(
            content.get("music_mood", "")
        )

        if not moods:

            moods = list(DEFAULT_MUSIC_MOODS)

        self.log(
            "Music mood: " + " ".join(moods)
        )

        return {
            "title": title,
            "summary": summary,
            "mood": moods,
            "visuals": cleaned_visuals,
        }


def write_content_files(episode_directory, content):

    """
    Saves the structured content as content.json plus a human-readable
    prompt.txt (TITLE / PROMPT / SUMMARY) that the web UI and the
    upload modals read.
    """

    episode_directory = Path(episode_directory)

    episode_directory.mkdir(parents=True, exist_ok=True)

    content_path = episode_directory / "content.json"

    with open(content_path, "w", encoding="utf-8") as file:

        json.dump(content, file, indent=2, ensure_ascii=False)

    divider = "=" * 72

    prompt_path = episode_directory / "prompt.txt"

    # IMPORTANT: TITLE, PROMPT, and SUMMARY must all be in the
    # same section between dividers so parse_prompt_file can find
    # them when it splits the file by the divider string.
    lines = [
        divider,
        f"TITLE: {content['title']}",
        f"PROMPT: {ContentGenerator.location_caption(content.get('title', ''))}",
        f"SUMMARY: {content['summary']}",
        divider,
        ""
    ]

    prompt_path.write_text("\n".join(lines), encoding="utf-8")

    return content_path


def read_content_file(episode_directory):

    content_path = Path(episode_directory) / "content.json"

    if not content_path.is_file():

        return None

    with open(content_path, "r", encoding="utf-8") as file:

        return json.load(file)