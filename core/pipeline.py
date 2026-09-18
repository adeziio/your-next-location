from pathlib import Path

from core.config_loader import ConfigLoader

from ai.content_generator import (
    ContentGenerator,
    write_content_files,
    read_content_file
)

from production.video import ProductionPipeline


OUTPUT_ROOT = (
    Path(
        "media"
    )
    /
    "output"
    /
    "shorts"
)


class LocationPipeline:

    """
    The Your Next Location production pipeline.

        Create episode
            -> Prompt stage: AI chooses a destination (or uses the one
               given) and writes the upload title + summary, the music
               mood and the footage search queries
            -> Generate Video stage: stock footage, licensed music,
               location intro caption, render, validation

    The destination is the entire content of the video - no narration,
    no script, no facts. This pipeline only enforces the structured
    output and the technical production requirements.
    """

    def __init__(
        self
    ):

        loader = ConfigLoader()

        self.config = (
            loader.load_all()
        )

        self.content_generator = (
            ContentGenerator(
                self.config
            )
        )

        self.production = (
            ProductionPipeline(
                self.config
            )
        )

        self.progress_callback = None

    def set_progress_callback(
        self,
        callback
    ):

        """
        Registers a progress callback invoked as
        callback(percent, message, stage) where stage is
        "prompt" or "video".

        The callback is also propagated to the production pipeline
        so that video-stage sub-progress reaches the web UI.
        """

        self.progress_callback = (
            callback
        )

        if self.production is not None:
            self.production.set_progress_callback(
                callback
            )

    def _notify(
        self,
        percent,
        message,
        stage
    ):

        if self.progress_callback is None:

            print(
                f"[{stage.upper()} {percent}%] {message}"
            )

            return

        try:

            self.progress_callback(
                int(
                    percent
                ),
                str(
                    message
                ),
                stage
            )

        except Exception:

            pass

    @staticmethod
    def project_root():

        return (
            Path(
                __file__
            )
            .resolve()
            .parents[1]
        )
    def _next_episode_directory(
        self
    ):

        output_root = (
            self.project_root()
            /
            OUTPUT_ROOT
        )

        output_root.mkdir(
            parents=True,
            exist_ok=True
        )

        next_number = 1

        for entry in output_root.iterdir():

            if (
                entry.is_dir()
                and entry.name.isdigit()
            ):

                next_number = max(
                    next_number,
                    int(
                        entry.name
                    )
                    + 1
                )

        episode_directory = (
            output_root
            /
            f"{next_number:03d}"
        )

        episode_directory.mkdir(
            parents=True,
            exist_ok=True
        )

        return episode_directory

    @staticmethod
    def resolve_episode_directory(
        episode_id
    ):

        """
        Accepts an episode id like "media/output/shorts/3" or
        just "3" and returns the episode directory.
        """

        episode_id = str(
            episode_id or ""
        ).strip()

        if not episode_id:

            raise ValueError(
                "Episode ID is required."
            )

        if episode_id.isdigit():

            episode_directory = (
                LocationPipeline.project_root()
                /
                OUTPUT_ROOT
                /
                episode_id
            )

        else:

            episode_directory = (
                LocationPipeline.project_root()
                /
                episode_id
            )

            allowed_root = (
                LocationPipeline.project_root()
                /
                OUTPUT_ROOT
            ).resolve()

            if (
                allowed_root
                not in episode_directory.resolve().parents
            ):

                raise ValueError(
                    "Invalid episode path."
                )

        if not episode_directory.is_dir():

            raise ValueError(
                "Episode does not exist."
            )

        return episode_directory

    def _generate_content(
        self,
        episode_directory,
        instruction=None
    ):

        self._notify(
            5,
            "Generating episode content...",
            "prompt"
        )

        content = (
            self.content_generator.generate(
                instruction
            )
        )

        self._notify(
            85,
            "Saving episode content...",
            "prompt"
        )

        write_content_files(
            episode_directory,
            content
        )

        self._notify(
            100,
            "Episode content ready.",
            "prompt"
        )

        return content

    def create_episode(
        self,
        prompt_only=False,
        instruction=None
    ):

        episode_directory = (
            self._next_episode_directory()
        )

        content = (
            self._generate_content(
                episode_directory,
                instruction
            )
        )

        if prompt_only:

            return {
                "episode_id": episode_directory.name,
                "episode_path": str(
                    episode_directory.relative_to(
                        self.project_root()
                    )
                ),
                "content": content
            }

        video = (
            self.production.run(
                episode_directory,
                content
            )
        )

        return {
            "episode_id": episode_directory.name,
            "episode_path": str(
                episode_directory.relative_to(
                    self.project_root()
                )
            ),
            "content": content,
            "video": video
        }

    def create_prompt(
        self,
        episode_id=None,
        instruction=None
    ):

        if episode_id:

            episode_directory = (
                self.resolve_episode_directory(
                    episode_id
                )
            )

        else:

            episode_directory = (
                self._next_episode_directory()
            )

        content = (
            self._generate_content(
                episode_directory,
                instruction
            )
        )

        return {
            "episode_id": episode_directory.name,
            "episode_path": str(
                episode_directory.relative_to(
                    self.project_root()
                )
            ),
            "content": content
        }

    def generate_video_from_prompt(
        self,
        prompt_item,
        episode_id
    ):

        """
        Runs the full video production for an existing episode.
        The structured content saved by the Prompt stage is the
        source of truth; prompt_item is only a fallback for
        episodes created before content.json existed.
        """

        episode_directory = (
            self.resolve_episode_directory(
                episode_id
            )
        )

        content = (
            read_content_file(
                episode_directory
            )
        )

        if content is None:

            prompt_item = (
                prompt_item
                if isinstance(
                    prompt_item,
                    dict
                )
                else {}
            )

            # Fallback for episodes generated before content.json
            # existed: the prompt.txt values are all we have, so the
            # destination is rebuilt from them. Without a destination
            # there is nothing to search footage for.
            title = str(
                prompt_item.get(
                    "title",
                    ""
                )
            ).strip()

            caption = str(
                prompt_item.get(
                    "prompt",
                    ""
                )
            ).strip()

            if not title and caption:

                title = caption.lstrip(
                    "\U0001F4CD "
                ).strip()

            if not title:

                raise ValueError(
                    "Episode has no generated content. "
                    "Generate the prompt first."
                )

            content = {
                "title": (
                    title
                    if title.startswith("\U0001F4CD")
                    else f"\U0001F4CD {title}"
                ),
                "location_caption": (
                    ContentGenerator.location_caption(
                        title
                    )
                ),
                "summary": str(
                    prompt_item.get(
                        "summary",
                        ""
                    )
                ),
                "mood": [],
                "visuals": []
            }

        if not content.get("visuals"):

            raise ValueError(
                "Episode has no visual search queries. "
                "Regenerate the prompt first."
            )

        video = (
            self.production.run(
                episode_directory,
                content
            )
        )

        return {
            "episode_id": episode_directory.name,
            "episode_path": str(
                episode_directory.relative_to(
                    self.project_root()
                )
            ),
            "video": video
        }
