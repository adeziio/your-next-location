from pathlib import Path

from moviepy import (
    VideoFileClip,
    AudioFileClip,
    CompositeVideoClip,
    CompositeAudioClip,
    vfx,
    afx
)

from production.captions import (
    LocationCaptionBuilder,
    LocationCaptionError
)


class CompositionError(
    RuntimeError
):

    pass


# Below this per-clip share (seconds), a segment keeps a single clip
# instead of splitting into sub-second cuts.
MIN_CLIP_SHARE = 1.0


class Composer:

    """
    Composes the final vertical travel Short.

    The video has no narration: the timeline is the configured target
    duration, footage fills it, the licensed music is the entire
    soundtrack, and the only text is the opening location caption
    ("📍 City, Country") built by production.captions.
    """

    def __init__(
        self,
        config
    ):

        self.config = config

        app_config = (
            config.get(
                "app",
                {}
            )
        )

        self.shorts_config = (
            app_config.get(
                "shorts",
                {}
            )
        )

        self.audio_config = (
            app_config.get(
                "audio",
                {}
            )
        )

        self.caption_builder = (
            LocationCaptionBuilder(
                config
            )
        )

        self.project_root = (
            Path(
                __file__
            )
            .resolve()
            .parents[1]
        )

    def compose(
        self,
        episode_directory,
        footage_groups,
        duration=None,
        visuals=None,
        segment_count=None,
        location_caption=None,
    ):

        """
        Renders episode.mp4 into the episode directory.

        footage_groups  : list of lists of downloaded footage paths,
                          where footage_groups[N] holds the clips
                          downloaded for visual N (one group per search
                          query). Each group is cycled through to cover
                          its own segment of the timeline.
        duration        : total video length in seconds (defaults to
                          app.shorts.target_duration_seconds).
        visuals         : the visuals list from the content, used for
                          the default segment count.
        segment_count   : how many footage segments the timeline is
                          split into (defaults to len(visuals)).
        location_caption: the opening caption line, e.g.
                          "📍 New York City, USA". This is the only
                          text in the video.
        """

        all_clips = [
            p
            for group in footage_groups
            for p in group
        ]

        if not all_clips:

            raise CompositionError(
                "No stock footage was downloaded, so the "
                "video cannot be composed."
            )

        width = int(
            self.shorts_config.get(
                "resolution",
                {}
            ).get(
                "width",
                1080
            )
        )

        height = int(
            self.shorts_config.get(
                "resolution",
                {}
            ).get(
                "height",
                1920
            )
        )

        fps = int(
            self.shorts_config.get(
                "fps",
                30
            )
        )

        if duration is None:

            duration = self.shorts_config.get(
                "target_duration_seconds",
                50
            )

        video_duration = float(
            duration
        )

        if video_duration <= 0:

            raise CompositionError(
                "The target video duration must be "
                "greater than zero."
            )

        output_path = (
            Path(
                episode_directory
            )
            /
            "episode.mp4"
        )

        self._opened_sources = []

        try:

            frame_clips = (
                self._build_frame_clips(
                    footage_groups,
                    video_duration,
                    width,
                    height,
                    segment_count,
                    visuals=visuals,
                )
            )

            intro_clips = (
                self._build_caption(
                    location_caption,
                    width,
                    height
                )
            )

            audio = (
                self._build_audio(
                    video_duration,
                    Path(
                        episode_directory
                    )
                )
            )

            final = CompositeVideoClip(
                frame_clips + intro_clips,
                size=(
                    width,
                    height
                )
            ).with_duration(
                video_duration
            )

            if audio is not None:

                final = final.with_audio(
                    audio
                )

            try:

                final.write_videofile(
                    str(
                        output_path
                    ),
                    fps=fps,
                    codec="libx264",
                    audio_codec="aac",
                    audio_bitrate="192k",
                    preset="medium",
                    threads=4,
                    logger=None
                )

            except Exception as error:

                raise CompositionError(
                    f"Video rendering failed: {error}"
                )

            finally:

                self._close(
                    final
                )

                self._close(
                    audio
                )

        finally:

            for source in (
                self._opened_sources
            ):

                self._close(
                    source
                )

        return output_path

    def _close(
        self,
        clip
    ):

        try:

            clip.close()

        except Exception:

            pass

    def _build_frame_clips(
        self,
        footage_groups,
        video_duration,
        width,
        height,
        segment_count,
        visuals=None,
    ):

        """
        Splits the timeline into one segment per visual search query and
        fills each segment with the clips downloaded for that query.

        footage_groups[i] holds the clips for visuals[i], so the footage
        shown at any moment always matches the query that produced it.
        Every downloaded clip of a group is used, dividing the segment
        equally between them; very short segments keep a single clip so
        no sub-second cuts are created.
        """

        if segment_count is None or segment_count < 1:

            if visuals:

                segment_count = len(visuals)

            else:

                segment_count = sum(
                    len(group)
                    for group in footage_groups
                )

        total_clips = sum(
            len(group)
            for group in footage_groups
        )

        segment_count = min(
            segment_count,
            max(total_clips, 1) * 4,
        )

        num_groups = len(footage_groups)

        if num_groups == 0:

            raise CompositionError(
                "No footage groups were supplied, so the video "
                "cannot be composed."
            )

        # Fallback pool: the first non-empty group, used only if a
        # group is empty (the provider stage requires every query to
        # return clips, so this should never happen in practice).
        fallback_pool = []

        for group in footage_groups:

            if group:

                fallback_pool = group

                break

        if not fallback_pool:

            raise CompositionError(
                "None of the footage groups contained clips."
            )

        segment_duration = video_duration / segment_count

        # Track per-group clip consumption so each query's clips are
        # cycled independently instead of one global index.
        group_counters = [0] * num_groups

        frame_clips = []

        for index in range(segment_count):

            start = index * segment_duration

            needed = min(
                segment_duration,
                video_duration - start
            )

            if needed <= 0:

                break

            group_idx = index % num_groups

            pool = footage_groups[group_idx]

            if not pool:

                pool = fallback_pool

            usable_count = len(pool)

            if needed / usable_count < MIN_CLIP_SHARE:

                usable_count = 1

            share = needed / usable_count

            for _ in range(usable_count):

                clip_index = (
                    group_counters[group_idx]
                    % len(pool)
                )

                group_counters[group_idx] += 1

                source_path = pool[clip_index]

                clip = VideoFileClip(
                    str(
                        source_path
                    )
                )

                self._opened_sources.append(
                    clip
                )

                try:

                    if clip.duration > share + 0.05:

                        clip = clip.subclipped(
                            0,
                            share
                        )

                    elif clip.duration < share - 0.05:

                        clip = clip.with_effects(
                            [
                                vfx.Loop(
                                    duration=share
                                )
                            ]
                        )

                    clip = (
                        self._fit_to_frame(
                            clip,
                            width,
                            height
                        )
                    )

                    clip = (
                        clip.without_audio()
                        .with_start(
                            start
                        )
                        .with_duration(
                            share
                        )
                    )

                    frame_clips.append(
                        clip
                    )

                except Exception:

                    self._close(
                        clip
                    )

                    raise

                start += share

        if not frame_clips:

            raise CompositionError(
                "No usable footage segments could be "
                "prepared."
            )

        return frame_clips

    def _fit_to_frame(
        self,
        clip,
        width,
        height
    ):

        """
        Center-crops the footage to the Shorts aspect ratio and
        scales it to the output resolution.
        """

        clip_width, clip_height = (
            clip.size
        )

        target_ratio = (
            width
            / height
        )

        source_ratio = (
            clip_width
            / clip_height
        )

        if source_ratio > target_ratio:

            crop_width = int(
                clip_height
                * target_ratio
            )

            clip = clip.cropped(
                width=crop_width
            )

        elif source_ratio < target_ratio:

            crop_height = int(
                clip_width
                / target_ratio
            )

            clip = clip.cropped(
                height=crop_height
            )

        if (
            clip.size[0] != width
            or clip.size[1] != height
        ):

            clip = clip.resized(
                (
                    width,
                    height
                )
            )

        return clip

    def _build_caption(
        self,
        location_caption,
        width,
        height
    ):

        """
        Builds the opening location caption (the only text in the
        video) and overlays it on top of the footage for the first
        few seconds.
        """

        if not self.caption_builder.enabled():

            return []

        caption = str(
            location_caption or ""
        ).strip()

        if not caption:

            return []

        try:

            return (
                self.caption_builder.build(
                    caption,
                    width,
                    height
                )
            )

        except LocationCaptionError as error:

            raise CompositionError(
                f"The location caption could not be built: {error}"
            )

    def _build_audio(
        self,
        video_duration,
        episode_directory
    ):

        """
        Builds the soundtrack for the video.

        A destination Short has no narration, so the licensed
        background music is the entire soundtrack. Returns None when
        no track is available; the video is still rendered without
        audio rather than failing.
        """

        audio_tracks = []

        music_clip = (
            self._build_music(
                video_duration,
                episode_directory
            )
        )

        if music_clip is not None:

            audio_tracks.append(
                music_clip
            )

        if not audio_tracks:

            return None

        return CompositeAudioClip(
            audio_tracks
        ).with_duration(
            video_duration
        )

    def _normalize_audio(
        self,
        audio_clip,
        target_db=-16.0,
        peak_limit=-1.0
    ):
        """
        Normalize an audio clip to a target RMS level and limit the peak.

        Every FreeSafeMusic source track lands at a different loudness
        level. We normalize each track so the volume multiplier in config
        has a predictable effect on the final heard volume.

        Parameters
        ----------
        audio_clip : AudioFileClip
            The music clip to normalize.
        target_db : float
            Target RMS level in dB (e.g. -16.0 for -16 dBFS RMS).
        peak_limit : float
            Maximum allowed peak in dB (e.g. -1.0 for -1 dBFS).

        Returns
        -------
        AudioFileClip
            The normalized clip.
        """
        import numpy as np

        # Get the audio as a numpy array using to_soundarray
        # Sample at the clip's native fps
        audio_array = audio_clip.to_soundarray(
            fps=audio_clip.fps,
            quantize=False
        )

        if audio_array.size == 0:
            return audio_clip

        # Calculate current RMS
        rms = np.sqrt(np.mean(audio_array ** 2))

        # Handle silent audio
        if rms < 1e-10:
            return audio_clip

        # Calculate target linear gain
        target_linear = 10 ** (target_db / 20.0)
        gain_factor = target_linear / rms

        # Clamp gain to avoid extreme values
        gain_factor = max(0.01, min(gain_factor, 100.0))

        # Apply the gain
        normalized_clip = audio_clip.with_volume_scaled(gain_factor)

        # Check peak after normalization
        # Use to_soundarray for peak detection (avoid ambiguous max_volume API)
        peak_array = normalized_clip.to_soundarray(
            fps=audio_clip.fps,
            quantize=False
        )

        if peak_array.size > 0:
            peak = float(np.max(np.abs(peak_array)))
        else:
            peak = 0.0

        if peak > 0:
            target_peak_linear = 10 ** (peak_limit / 20.0)

            if peak > target_peak_linear:
                # Apply peak limiting
                limiter_gain = target_peak_linear / peak
                normalized_clip = normalized_clip.with_volume_scaled(limiter_gain)

        return normalized_clip

    def _build_music(
        self,
        video_duration,
        episode_directory
    ):

        """
        Mixes the licensed background music downloaded for this
        episode by the music provider into the episode's music/
        directory. The track is looped to the video length and faded
        in/out so the Short starts and ends cleanly. The file itself
        is temporary - the production pipeline deletes it after the
        render and keeps the license metadata.
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

            return None

        music_directory = (
            Path(
                episode_directory
            )
            /
            "music"
        )

        music_files = (
            self._list_audio_files(
                music_directory
            )
        )

        if not music_files:

            return None

        music_path = (
            music_files[0]
        )

        try:

            music_clip = AudioFileClip(
                str(
                    music_path
                )
            )

            # Loudness normalization: every FreeSafeMusic source track lands
            # at a different level (-18 dB to -8 dB RMS). We normalize each
            # track to a target RMS so that the volume multiplier in config
            # has a predictable effect on the final loudness. Without this,
            # the same 0.45 setting produces wildly different heard volumes.
            norm_target = float(
                music_config.get(
                    "normalize_rms",
                    -16.0
                )
            )

            music_clip = self._normalize_audio(
                music_clip,
                target_db=norm_target,
                peak_limit=-1.0
            )

            looped = music_clip.with_effects(
                [
                    afx.AudioLoop(
                        duration=video_duration
                    )
                ]
            ).with_volume_scaled(
                float(
                    music_config.get(
                        "volume",
                        1.0
                    )
                )
            )

            effects = []

            try:

                fade_in = float(
                    music_config.get(
                        "fade_in_seconds",
                        0
                    )
                    or 0
                )

                fade_out = float(
                    music_config.get(
                        "fade_out_seconds",
                        0
                    )
                    or 0
                )

            except (TypeError, ValueError):

                fade_in = 0.0

                fade_out = 0.0

            if fade_in > 0:

                effects.append(
                    afx.AudioFadeIn(
                        min(fade_in, video_duration / 2)
                    )
                )

            if fade_out > 0:

                effects.append(
                    afx.AudioFadeOut(
                        min(fade_out, video_duration / 2)
                    )
                )

            if effects:

                looped = looped.with_effects(
                    effects
                )

            return looped

        except Exception:

            self._close(
                music_clip
            )

            raise

    def _list_audio_files(
        self,
        directory
    ):

        directory = Path(
            directory
        )

        if not directory.is_absolute():

            directory = (
                self.project_root
                /
                directory
            )

        if not directory.is_dir():

            return []

        extensions = (
            ".mp3",
            ".wav",
            ".m4a",
            ".ogg"
        )

        files = [
            path
            for path in directory.iterdir()
            if path.is_file()
            and path.suffix.lower()
            in extensions
        ]

        return sorted(
            files
        )