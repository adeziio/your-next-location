from pathlib import Path

import json

from production.footage import (
    create_video_provider,
    VideoProviderError
)

from production.music import (
    create_music_provider,
    MusicProviderError
)

from production.captions import (
    LOCATION_PIN
)

from production.composer import (
    Composer,
    CompositionError
)


class ProductionError(
    RuntimeError
):

    pass


class ProductionPipeline:

    """
    The Generate Video stage. Turns the generated destination content
    into a finished, validated vertical travel Short:

        1. Retrieve stock footage for every visual search query
        2. Select and download legally usable background music
        3. Compose the footage, the location caption and the music
        4. Render and validate the final video

    The destination is the content: there is no narration and no
    subtitles, so the timeline comes from
    app.shorts.target_duration_seconds and every clip in it shows the
    destination the queries asked for.
    """

    def __init__(
        self,
        config,
        progress_callback=None
    ):

        self.config = config

        self.app_config = (
            config.get(
                "app",
                {}
            )
        )

        self.shorts_config = (
            self.app_config.get(
                "shorts",
                {}
            )
        )

        self.audio_config = (
            self.app_config.get(
                "audio",
                {}
            )
        )

        self.progress_callback = (
            progress_callback
        )

        self._last_progress = 0

        self.composer = Composer(
            config
        )

    def set_progress_callback(
        self,
        callback
    ):

        self.progress_callback = (
            callback
        )

    def update_progress(
        self,
        percent,
        message
    ):

        percent = int(
            max(
                0,
                min(
                    100,
                    percent
                )
            )
        )

        # The final rendered state is authoritative, so the video
        # progress must never regress. Intermediate notifications
        # from sub-steps (providers, downloads, rendering) can
        # otherwise pull the bar backwards.

        if (
            percent < 100
            and percent
            <= self._last_progress
        ):

            percent = (
                self._last_progress
            )

        else:

            self._last_progress = (
                percent
            )

        if self.progress_callback is None:

            print(
                f"[VIDEO {percent}%] {message}"
            )

            return

        try:

            self.progress_callback(
                percent,
                str(
                    message
                ),
                "video"
            )

        except Exception:

            pass

    def _progress_between(
        self,
        start_percent,
        end_percent,
        index,
        total
    ):

        """
        Interpolates a progress value between two stage percents
        for item `index` of `total`, so sub-step notifications map
        onto the overall stage range.
        """

        total = max(
            1,
            int(
                total
            )
        )

        index = max(
            0,
            min(
                index,
                total
            )
        )

        fraction = (
            index
            /
            total
        )

        return int(
            round(
                start_percent
                +
                (
                    end_percent
                    - start_percent
                )
                * fraction
            )
        )

    def run(
        self,
        episode_directory,
        content
    ):

        episode_directory = Path(
            episode_directory
        )

        episode_directory.mkdir(
            parents=True,
            exist_ok=True
        )

        if not isinstance(
            content,
            dict
        ):

            raise ProductionError(
                "Episode content is missing."
            )

        visuals = content.get(
            "visuals",
            []
        )

        if not visuals:

            raise ProductionError(
                "Episode content has no visual search queries, "
                "so there is no destination footage to show."
            )

        # The title IS the destination and already carries the pin
        # ("📍 City, Country") - it doubles as the on-screen caption.
        # Legacy episodes still carrying destination/location_caption
        # fields keep working.
        location_caption = str(
            content.get(
                "location_caption",
                ""
            )
        ).strip()

        if not location_caption:

            title = str(
                content.get(
                    "title",
                    ""
                )
            ).strip()

            if title:

                location_caption = (
                    title
                    if title.startswith(LOCATION_PIN)
                    else f"{LOCATION_PIN} {title}"
                )

            else:

                destination = str(
                    content.get(
                        "destination",
                        ""
                    )
                ).strip()

                if destination:

                    location_caption = (
                        destination
                        if destination.startswith(LOCATION_PIN)
                        else f"{LOCATION_PIN} {destination}"
                    )

        # 1. Stock footage through the configured provider
        self.update_progress(
            5,
            "Searching for destination footage..."
        )

        footage_groups = (
            self._collect_footage(
                episode_directory,
                visuals
            )
        )

        # 2. Licensed background music via the configured provider
        music_metadata = (
            self._collect_music(
                episode_directory,
                content
            )
        )

        # 3. Compose and render
        self.update_progress(
            72,
            "Composing the final video..."
        )

        try:

            output_path = (
                self.composer.compose(
                    episode_directory,
                    footage_groups,
                    duration=(
                        self.shorts_config.get(
                            "target_duration_seconds",
                            50
                        )
                    ),
                    visuals=visuals,
                    segment_count=len(
                        visuals
                    ),
                    location_caption=location_caption,
                )
            )

            self.update_progress(
                96,
                "Rendering complete."
            )

        finally:

            # The music track was only rented for this render -
            # delete the temporary file and keep the metadata.
            self._cleanup_music(
                music_metadata
            )

        if output_path.exists():

            self.update_progress(
                100,
                "Render complete."
            )

            return {
                "video_path": str(output_path),
                "title": str(
                    content.get(
                        "title",
                        ""
                    )
                ),
            }

        raise ProductionError(
            "The video render finished without producing "
            "a file."
        )

    def _collect_footage(
        self,
        episode_directory,
        visuals
    ):

        """
        Downloads the clips for every visual search query.

        Each query keeps its own footage/ subdirectory, so re-running an
        episode reuses already downloaded clips instead of fetching them
        again. Every query must return candidates_per_query clips: a
        download failure fails video generation instead of silently
        producing a video with holes in it.
        """

        footage_directory = (
            episode_directory
            / "footage"
        )

        query_percent = {"value": 5}

        provider = (
            create_video_provider(
                self.config,
                notify=(
                    lambda message:
                    self.update_progress(
                        query_percent["value"],
                        message,
                    )
                ),
            )
        )

        candidates_per_query = int(
            self._provider_setting(
                "candidates_per_query",
                2,
            )
        )

        footage_groups = []

        total_queries = len(visuals)

        # Track Pexels video IDs already downloaded this episode so
        # different search queries do not produce duplicate clips.
        downloaded_ids = set()

        for index, visual in enumerate(visuals, start=1):

            query = str(
                visual.get(
                    "search_query",
                    ""
                )
            ).strip()

            if not query:

                raise VideoProviderError(
                    f"Visual {index}/{total_queries} has no search "
                    "query; cannot download footage for it."
                )

            self.update_progress(
                self._progress_between(
                    8, 62, index - 1, total_queries
                ),
                f"Footage {index}/{total_queries}: {query}",
            )

            query_percent["value"] = self._progress_between(
                8, 62, index - 1, total_queries
            )

            query_directory = (
                footage_directory
                / provider.slugify(query)
            )

            try:

                downloaded = provider.fetch(
                    query,
                    query_directory,
                    max_videos=candidates_per_query,
                    downloaded_ids=downloaded_ids,
                )

            except VideoProviderError:

                raise

            except Exception as error:

                raise VideoProviderError(
                    f"Footage collection failed for '{query}': {error}"
                ) from error

            if len(downloaded) < candidates_per_query:

                raise VideoProviderError(
                    f"Only {len(downloaded)}/{candidates_per_query} clips "
                    f"downloaded for '{query}'."
                )

            footage_groups.append(
                list(downloaded)
            )

        if not footage_groups:

            raise VideoProviderError(
                "No visual search queries available to download "
                "footage for."
            )

        return footage_groups

    def _collect_music(
        self,
        episode_directory,
        content
    ):

        """
        Selects and downloads one licensed background track that fits
        the destination's mood. Music is treated as optional: if a
        provider cannot be reached, the episode still renders (without
        music) instead of failing.

        The track file lands in the episode's music/ directory together
        with a metadata.json recording where it came from and under
        which license it may be used.
        """

        music_config = (
            self.audio_config.get(
                "music",
                {}
            )
        )

        if not music_config.get(
            "enabled",
            True
        ):

            self.update_progress(
                64,
                "Background music is disabled."
            )

            return None

        self.update_progress(
            64,
            "Selecting background music..."
        )

        mood_tags = (
            content.get(
                "mood"
            )
            if isinstance(
                content,
                dict
            )
            else []
        )

        if not isinstance(
            mood_tags,
            list
        ):

            mood_tags = []

        try:

            provider = (
                create_music_provider(
                    self.config,
                    notify=(
                        lambda message:
                        self.update_progress(
                            66,
                            message
                        )
                    )
                )
            )

            metadata = provider.fetch(
                mood_tags,
                episode_directory
                /
                "music"
            )

        except Exception as error:

            # Music is optional - a provider failure must never stop
            # the episode from being produced.
            self.update_progress(
                68,
                f"Background music unavailable: {error}"
            )

            return None

        if metadata is None:

            self.update_progress(
                68,
                "No background music found; "
                "continuing without music."
            )

            return None

        metadata_path = (
            episode_directory
            /
            "music"
            /
            "metadata.json"
        )

        try:

            with open(
                metadata_path,
                "w",
                encoding="utf-8"
            ) as file:

                json.dump(
                    metadata,
                    file,
                    indent=2,
                    ensure_ascii=False
                )

        except Exception:

            pass

        self.update_progress(
            70,
            "Background music ready: "
            f"{metadata.get('title', 'unknown')}"
        )

        return metadata

    def _cleanup_music(
        self,
        music_metadata
    ):

        """
        Deletes the temporarily downloaded music file after the
        render. The metadata.json next to it is kept so every
        episode records which track was used and under which
        license.
        """

        if not isinstance(
            music_metadata,
            dict
        ):

            return

        track_path = music_metadata.get(
            "file"
        )

        if not track_path:

            return

        try:

            Path(
                track_path
            ).unlink(
                missing_ok=True
            )

        except Exception:

            pass

    def _provider_setting(
        self,
        name,
        default
    ):

        value = (
            self.config.get(
                "pexels",
                {}
            )
            .get(
                name,
                default
            )
        )

        try:

            return int(
                value
            )

        except (
            TypeError,
            ValueError
        ):

            return default