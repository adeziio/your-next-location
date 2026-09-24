r"""
Smoke test for the Your Next Location pipeline.

Validates the core production path without requiring API keys or a running
Ollama server:

    1. Load all configuration files.
    2. Build a ContentGenerator and verify it produces a structured destination.
    3. Build a LocationCaptionBuilder and verify the caption clips render.

Run with:

    .venv\Scripts\activate && python -m pytest tests\smoke_test.py -v
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _load_config():
    from core.config_loader import ConfigLoader

    loader = ConfigLoader()
    config = loader.load_all()
    assert config is not None, "ConfigLoader returned None"
    assert "app" in config
    assert "content" in config
    assert "pexels" in config
    assert "freesafemusic" in config
    return config


def test_config_loads():
    """All config files load and contain expected sections."""
    config = _load_config()
    assert config["app"]["video_provider"] == "pexels"
    assert config["app"]["music_provider"] == "freesafemusic"
    assert config["app"]["shorts"]["resolution"]["width"] == 1080
    assert config["app"]["shorts"]["resolution"]["height"] == 1920


def test_location_caption_builder():
    """LocationCaptionBuilder produces the caption's character clips."""
    config = _load_config()
    from production.captions import LocationCaptionBuilder

    builder = LocationCaptionBuilder(config)
    assert builder.enabled() is True
    assert builder.duration() == 5.0

    clips = builder.build(
        caption="\U0001F4CD New York City, USA",
        width=1080,
        height=1920,
    )
    assert isinstance(clips, list)
    assert len(clips) > 0, "Expected at least one character clip"
    # Each clip should be 5 seconds long (the intro duration)
    assert abs(clips[0].duration - 5.0) < 0.1, (
        f"Expected intro clip duration ~5s, got {clips[0].duration}"
    )
    # Each character clip is a cropped piece of the rendered line
    assert clips[0].h > 0
    assert clips[0].w > 0

    for clip in clips:
        clip.close()


def test_content_generator_structure():
    """ContentGenerator enforces the travel Short output schema."""
    from ai.content_generator import ContentGenerator, build_schema

    config = _load_config()
    generator = ContentGenerator(config)

    # location_caption is a static method that prepends the pin emoji
    # and is idempotent - the emoji can never be doubled
    assert hasattr(ContentGenerator, "location_caption")
    caption = ContentGenerator.location_caption("New York City, USA")
    assert caption == "\U0001F4CD New York City, USA"
    assert (
        ContentGenerator.location_caption(caption)
        == "\U0001F4CD New York City, USA"
    )

    # The schema must include the travel-specific fields
    schema = build_schema(generator.visual_count())
    required = schema.get("required", [])
    assert "title" in required
    assert "summary" in required
    assert "visuals" in required
    assert "music_mood" in required
    # destination/location_caption are gone - the title IS the
    # destination, and the caption is derived from it
    assert "destination" not in schema.get("properties", {})
    assert "location_caption" not in schema.get("properties", {})
    # No narration field exists - the destination is the entire content
    assert "narration" not in schema.get("properties", {})
    assert "script" not in schema.get("properties", {})


def test_composer_imports():
    """The Composer class imports and references the caption builder."""
    from production.composer import Composer, CompositionError

    config = _load_config()
    composer = Composer(config)
    assert composer.caption_builder is not None


def test_production_pipeline_imports():
    """ProductionPipeline wires together footage, music, intro, and composer."""
    from production.video import ProductionPipeline, ProductionError

    config = _load_config()
    pipeline = ProductionPipeline(config)
    assert pipeline.composer is not None


def test_music_mood_genre_map_consistency():
    """allowed_music_moods, music_rules and MOOD_GENRE_MAP stay in sync."""
    config = _load_config()

    from ai.content_generator import ContentGenerator
    from production.music.freesafemusic import MOOD_GENRE_MAP

    generator = ContentGenerator(config)
    allowed = set(generator.allowed_music_moods)

    # Every allowed mood must map onto Free Safe Music genre pages and
    # vice versa - a mood without a mapping would silently pull
    # unrelated genre pages.
    assert allowed == set(MOOD_GENRE_MAP.keys()), (
        "allowed_music_moods (config/content.json) and MOOD_GENRE_MAP "
        "(production/music/freesafemusic.py) must list the same moods"
    )

    for mood, slugs in MOOD_GENRE_MAP.items():
        assert slugs, f"Mood '{mood}' maps to no genre slugs"
        for slug in slugs:
            assert " " not in slug and slug == slug.strip().lower(), (
                f"Mood '{mood}' has a malformed genre slug: '{slug}'"
            )

    # The culture-flavored moods that give locations their local sound.
    for mood in ("jazz", "funky", "disco", "folk", "tribal", "bossa nova"):
        assert mood in allowed

    # The hardcoded list inside music_rules must mention every allowed
    # mood, otherwise the model is instructed with a stale vocabulary.
    rules = config["content"]["content_generation"].get("music_rules", [])
    list_rule = next(
        (rule for rule in rules if "Use words from this list only" in rule),
        "",
    )
    assert list_rule, "music_rules is missing the allowed-words rule"
    for mood in generator.allowed_music_moods:
        assert mood in list_rule, (
            f"Mood '{mood}' is allowed but missing from the "
            "music_rules list"
        )


def test_normalize_mood():
    """_normalize_mood keeps allowed phrases and drops unknown words."""
    config = _load_config()

    from ai.content_generator import ContentGenerator

    generator = ContentGenerator(config)

    # Multi-word allowed moods are matched before single words.
    assert generator._normalize_mood("dreamy bossa nova chill") == [
        "bossa nova",
        "dreamy",
        "chill",
    ]
    # No phrase is assembled from fragments; unknown words are dropped.
    assert generator._normalize_mood("BossaNova") == []
    assert generator._normalize_mood("cinematic kpop tropical") == [
        "cinematic",
        "tropical",
    ]
    # Never more than three moods reach the provider.
    assert generator._normalize_mood("lofi chill bossa nova jazz") == [
        "bossa nova",
        "lofi",
        "chill",
    ]


if __name__ == "__main__":
    test_config_loads()
    print("PASS: config_loads")
    test_location_caption_builder()
    print("PASS: location_caption_builder")
    test_content_generator_structure()
    print("PASS: content_generator_structure")
    test_composer_imports()
    print("PASS: composer_imports")
    test_production_pipeline_imports()
    print("PASS: production_pipeline_imports")
    test_music_mood_genre_map_consistency()
    print("PASS: music_mood_genre_map_consistency")
    test_normalize_mood()
    print("PASS: normalize_mood")
    print("\nAll smoke tests passed.")
