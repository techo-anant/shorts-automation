# shorts-automation

A Python service that watches a Google Drive folder, finds short-worthy moments in long-form videos, cuts and reformats them to vertical 9:16, and uploads them as YouTube Shorts — fully automated.

**Drop a video in Drive. Get Shorts on YouTube.**

---

## How it works

1. **Ingest** — polling loop detects new videos in `<drive-root>/to-process/<category>/`
2. **Format probe** — Gemini 2.5 Flash samples 5 frames and classifies facecam presence, position, and aspect ratio
3. **Index** — TwelveLabs Pegasus 1.5 indexes the video (once; index reused on retry)
4. **Describe** — Pegasus describes the video in 15-second windows, returning structured JSON per window
5. **Select** — Gemini 2.5 reads the descriptions + source video and picks clips, merging consecutive windows freely into clips of any length; each clip gets a rank (1–10) and a one-sentence rationale
6. **Cut + reformat** — FFmpeg cuts each clip and reformats to 1080×1920 using the recipe for that video's format profile
7. **Approval** — Discord message lists all clips sorted by rank; operator approves or rejects each one
8. **Upload** — approved clips upload to YouTube as Shorts; source moves to `processed/`

Every step is cost-tracked (`CostEvent` rows), idempotent (safe to retry), and failure-isolated (one video failing doesn't block others).

---

## Why Pegasus, not Marengo

The original design used TwelveLabs Marengo for clip selection. After testing on real gameplay videos, Marengo was replaced with Pegasus 1.5 + Gemini 2.5. Three specific problems with Marengo:

1. **Wrong clip lengths.** Marengo's search API returns the entire video segmented into consecutive 5–8 second windows ranked by similarity to a query text. With a 60-second minimum, zero clips came back from a 33-minute video. With a 5-second minimum, 200+ un-shippable micro-clips came back.

2. **Wrong selection criteria.** On a Valorant gameplay video, the top-ranked "exciting moment" was the Valorant logo intro screen — high visual similarity to "Valorant", low actual interest. Marengo scores on what it can vectorize, not on what's clip-worthy.

3. **No way to express intent.** Marengo accepts a text query and returns similar-looking segments. There is no way to ask for "a sequence with multiple kills in a round" — it's a search model, not a reasoning model.

Pegasus 1.5 is a video-language model. It describes audio, visuals, and on-screen action for the full video. Gemini then reads those descriptions and reasons about what makes a good Short. The result: flexible clip lengths (12s, 45s, 90s — whatever the content warrants), intent-driven selection, and an auditable rationale for every clip.

Full technical rationale in [`docs/plan.md`](docs/plan.md).

---

## Tech stack

| Layer | Choice |
|---|---|
| Language | Python 3.11+ |
| Runtime | Async polling loop (`asyncio`), no web framework |
| Database | Postgres (Railway), SQLAlchemy 2.x async, Alembic migrations |
| Video understanding | TwelveLabs Pegasus 1.5 (describe) + Gemini 2.5 Flash (select + format probe) |
| Video processing | FFmpeg (cut, reformat, caption burn-in) |
| Storage | Google Drive API v3 (source videos, approval previews) |
| Upload | YouTube Data API v3 (`videos.insert`) |
| Notifications | Discord webhook |
| Deploy | Railway (Docker, auto-deploy from `main`) |
| Package manager | `uv` |

---

## Setup

### Prerequisites

- Python 3.11+
- `uv` — [install](https://docs.astral.sh/uv/getting-started/installation/)
- `ffmpeg` in PATH (or installed in the Docker image)
- A Railway project with a Postgres database
- Google Cloud project with Drive API and YouTube Data API v3 enabled
- TwelveLabs account with Pegasus 1.5 access
- Google AI Studio account (Gemini 2.5 Flash)
- Discord webhook URL

### Install dependencies

```bash
uv sync
```

### Environment variables

Copy `.env.example` and fill in all values:

```bash
cp .env.example .env
```

Required env vars:

| Variable | Description |
|---|---|
| `DATABASE_URL` | Postgres connection string |
| `GOOGLE_CLIENT_ID` | OAuth client ID (Drive + YouTube) |
| `GOOGLE_CLIENT_SECRET` | OAuth client secret |
| `GOOGLE_REFRESH_TOKEN` | Long-lived refresh token (see below) |
| `DRIVE_ROOT_FOLDER_ID` | ID of the Drive folder to watch |
| `YOUTUBE_REFRESH_TOKEN` | YouTube OAuth refresh token |
| `TWELVELABS_API_KEY` | TwelveLabs API key |
| `GEMINI_API_KEY` | Google AI Studio API key |
| `DISCORD_WEBHOOK_URL` | Discord webhook for operator alerts |

### Get OAuth tokens

Run the one-time local OAuth flow to generate `GOOGLE_REFRESH_TOKEN` and `YOUTUBE_REFRESH_TOKEN`:

```bash
uv run python scripts/get_refresh_token.py
```

### Run migrations

```bash
uv run alembic upgrade head
```

### Run locally

```bash
uv run python -m src.main
```

---

## Commands

| Task | Command |
|---|---|
| Install deps | `uv sync` |
| Run dev | `uv run python -m src.main` |
| Build image | `docker build -t yt-shorts .` |
| Test | `uv run pytest` |
| Lint / format | `uv run ruff check && uv run ruff format` |
| Type check | `uv run mypy src` |
| DB migrate | `uv run alembic upgrade head` |
| Get OAuth token | `uv run python scripts/get_refresh_token.py` |

---

## Repository structure

```
.
├── Dockerfile
├── railway.toml
├── pyproject.toml
├── uv.lock
├── .env.example
├── scripts/
│   └── get_refresh_token.py
├── src/
│   ├── main.py                 # polling loop + health server
│   ├── config.py               # env var loading (pydantic-settings)
│   ├── db/                     # SQLAlchemy models + Alembic migrations
│   ├── costs/                  # per-step cost calculators + CostEvent writers
│   ├── drive/                  # Google Drive API wrapper
│   ├── youtube/                # Shorts upload client
│   ├── ingest/                 # format probe (frames → Gemini Flash)
│   ├── indexer/                # Pegasus index lifecycle (create once, reuse)
│   ├── moments/                # clip selection (Pegasus describe → Gemini select)
│   ├── ffmpeg/                 # cut, reformat (blur-bg, webcam-split)
│   ├── workflow/               # per-source-video pipeline orchestration
│   ├── notifications/          # Discord webhook alerts
│   └── health/                 # Railway health-check server
├── tests/                      # mirrors src/ layout
├── prompts/                    # Pegasus and Gemini prompt templates by category
└── docs/
    ├── plan.md                 # full phased build plan + architectural decisions
    └── model-bakeoff.md        # Pegasus vs Marengo comparison results
```

---

## Drive folder structure

```
<drive-root>/
  to-process/
    gameplay/
    tutorial/
    podcast/
  processed/
    gameplay/
    ...
  failed/
    gameplay/
    ...
  no-shorts-found/
    gameplay/
    ...
  pending-approval/
    <source-video-name>/    # temporary; deleted after approval decision
```

Content category is determined by the subfolder name under `to-process/`. Unknown folder names default to `generic` with a Discord warning.

---

## Cost model

Approximate cost per 33-minute gameplay video:

| Step | Provider | Cost |
|---|---|---|
| Format probe (5 frames) | Gemini 2.5 Flash | ~$0.01 |
| Pegasus describe | TwelveLabs Pegasus 1.5 | ~$1.00 |
| Gemini clip select | Gemini 2.5 Flash | ~$0.05 |
| YouTube upload (per short) | YouTube Data API | quota units, not $ |

Circuit breaker: Discord warning at $5/source, hard stop at $8/source.

---

## Build status

| Phase | Description | Status |
|---|---|---|
| 1 | Scaffold (polling loop, health server, DB models, Discord) | Done |
| 2 | Drive round-trip + cost tracking | Done |
| 3 | Gemini format probe | Done |
| 4 | Marengo indexing + moment detection | Done (Marengo replaced in Phase 8) |
| 5 | Test backfill for v1 | Done |
| 6 | VideoIndex as long-lived DB asset | Done |
| 7 | Short DB model + per-short cost attribution | Done |
| 8 | Pegasus describe + Gemini clip selection | In progress |
| 9 | FFmpeg cut + reformat | Planned |
| 10 | Discord approval gate | Planned |
| 11 | YouTube Shorts upload | Planned |
| 12 | External API response persistence | Planned |
