# Your Next Location

A cinematic travel video automation project.

Every Short is one destination — a city, island, coastline, mountain town,
region, or country — shown through beautiful, atmospheric stock footage with
music that fits the place. **No narration, no facts, no subtitles, no
commentary: the destination itself is the content.**

The video opens with a single location caption (`📍 City, Country`) that types
itself on, holds, then retraces off. After that, the footage and music carry
the entire video.

## How it works

The pipeline is config-driven and fully automated:

1. **Prompt stage** — An LLM receives a destination (e.g. `New York City, USA`)
   or chooses one at random, then produces:
   - upload title + summary (metadata only, never shown on screen)
   - a music mood (1–3 keywords like `cinematic`, `lofi`, `tropical`)
   - 14 visual search queries (one per footage segment of the timeline)

2. **Video stage** — Stock footage is fetched from Pexel, licensed music is
   selected from Free Safe Music, and everything is assembled into a vertical
   Short with the location caption animation.

```
Destination → Search footage → Download clips → Select music →
Assemble footage → Add location caption → Mix audio → Export video
```

## Key features

- **Opening location caption**: a single horizontal line (`📍 City, Country`)
  that types left→right, holds, and retraces right→left, then disappears
  within 5 seconds.
- **Cinematic travel footage** from Pexels — skylines, streets, aerials,
  nature, cafes, sunsets, night scenes, etc.
- **Licensed background music** from Free Safe Music (cleared for commercial
  and monetized use on YouTube and Instagram — no attribution required).
- **Vertical Shorts** (1080×1920, 30 fps) with configurable target duration.
- **No narration, no voice, no subtitles.** The destination is the content.

## Quick start

### Prerequisites

- Python 3.12
- FFmpeg
- Google Chrome (for Pexel stock-footage downloads)
- Ollama + `qwen3:8b` model (for destination generation)

### Install

**Windows:**

```bat
setup.bat
```

### Run

**Windows:**

```bat
runner.bat
```

The web UI starts at http://localhost:8000.

### Command line

```bat
.venv\Scripts\activate
python main.py "New York City, USA"
```

Without a destination argument, the AI picks one at random.

## Configuration

All behaviour is driven by JSON files in `config/`:

| File | Purpose |
|------|---------|
| `app.json` | Video resolution, FPS, duration, audio, caption settings |
| `content.json` | Channel description, generation instruction, visual/music rules |
| `pexels.json` | Stock-footage provider settings (search counts, resolution, timeouts) |
| `freesafemusic.json` | Music provider settings (genres, preferred tags, timeouts) |
| `ai_models.json` | LLM model name, temperature, timeout, generation options |
| `youtube.json` | YouTube upload metadata defaults |
| `instagram.json` | Instagram publishing settings |

## Project structure

```
your-next-location/
├── config/              # JSON configuration files
├── ai/                  # LLM content generation (destination + search queries)
│   └── providers/       # LLM backends (Ollama)
├── core/                # Pipeline orchestration
├── production/          # Video assembly
│   ├── footage/         # Stock-footage providers (Pexel)
│   ├── music/           # Music providers (Free Safe Music)
│   ├── composer.py      # Assembles footage + caption + music into a Short
│   ├── captions.py      # Opening 📍 location caption animation
│   └── video.py         # ProductionPipeline: fetches footage/music, renders
├── web/                 # Web UI + HTTP API server
├── youtube/             # YouTube upload + metadata
├── instagram/           # Instagram publishing
├── media/               # Generated output (ignored by git)
└── tests/               # Smoke tests
```

## What's removed

Compared to Curious About Things, this project is intentionally simpler:

- ❌ Script generation / fact research
- ❌ Voice generation / narration
- ❌ Subtitles throughout the video
- ❌ Multiple content sections / complex storytelling
- ❌ Complicated visual effects

The basic formula: **Location + beautiful clips + good music = finished video.**

## License

The generated videos use:

- Stock footage from Pexels (CC0 / Pexels license)
- Background music from Free Safe Music (commercial-use cleared, no attribution required)
