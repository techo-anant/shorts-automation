# CLAUDE.md

> Persistent project context for AI coding assistants. Keep this file under ~500 lines. Update when conventions change or when a recurring problem gets solved (see "Lessons Learned" at the bottom).

---

## 1. Project

- **What it is:** A Python service that takes long-form videos from Google Drive and produces YouTube Shorts. It classifies the source video (gameplay/tutorial/etc. via folder, facecam presence via frame sampling), uses a video-understanding model to find short-worthy moments (<180s), cuts and reformats them to 9:16 with FFmpeg, optionally burns in captions on key beats, and uploads each short to YouTube.
- **Who it's for:** The same single YouTube creator (the operator) running the upload-automation service. Operator drops a long-form video into a Drive subfolder; the service produces and uploads 0–N shorts from it.
- **Core value:** Drop long video → fully uploaded Shorts with correct vertical formatting — no manual clipping, no manual reformatting, no double-uploaded clips.
- **Current version:** v0 (research + planning). No phases shipped yet. The first open question is **Pegasus (TwelveLabs) vs. Gemini 2.5 Flash** for moment detection — this must be resolved with a real-video bake-off before pipeline build-out hardens.
- **Relationship to upload-automation:** This is a **separate** service from the upload-automation project (`CLAUDE.md` for that lives in its own repo). Shared infrastructure (Drive client patterns, YouTube upload client, Discord notifier, Railway deploy shape) is **copied or extracted**, never imported across project boundaries. No description, no thumbnail, no chapters, no template system — the upload step here is a single API call.

## 2. Tech Stack

- **Language(s):** Python 3.11+
- **Framework(s):** No web framework. Async polling loop (`asyncio`). Tiny `aiohttp` (or `fastapi`) health-check server for Railway. Mirror the upload-automation shape.
- **Data layer:** Postgres on Railway. SQLAlchemy 2.x (async). Alembic for migrations. Schema is separate from upload-automation — do not share a database.
- **Key external services:**
  - Google Drive API v3 — folder watching, file download, file move
  - YouTube Data API v3 — Shorts upload via `videos.insert` (vertical + ≤60s + `#Shorts` is what YouTube uses to classify; no separate "shorts" endpoint exists — verify at integration time)
  - **TwelveLabs Pegasus 1.2 / 1.5** — video indexing + moment detection (primary candidate)
  - **Gemini 2.5 Flash** via `google-genai` SDK — facecam/format probe at ingest, moment-detection fallback, optional rating pass, optional caption-spec generation
  - **Replicate** — already-configured client; use it if either model is available there
  - **FFmpeg** — frame sampling, cutting, reformatting (blur-top-bottom, webcam-above-gameplay), caption burn-in
  - Discord webhook — operator alerts on failures and key events
- **Package manager / build tool:** `uv`. `pyproject.toml` as source of truth.
- **Container:** Docker on Railway. `ffmpeg` is **load-bearing here** (not just a fallback as in upload-automation) — pin a known version in the image and document it.

## 3. Repository Map

```
.
├── Dockerfile
├── railway.toml
├── pyproject.toml
├── uv.lock
├── .env.example
├── README.md
├── scripts/
│   └── get_refresh_token.py        # one-time local OAuth flow (Drive + YouTube)
├── src/
│   ├── main.py                     # entrypoint: polling loop + health server
│   ├── config.py                   # env var loading and validation (pydantic-settings)
│   ├── db/                         # models (SourceVideo, Short, MomentDetectionRun, CostEvent, QuotaUsage)
│   ├── costs/                      # per-step cost calculators + CostEvent writers (one fn per priced operation)
│   ├── drive/                      # Drive API wrapper, list/download/move ops
│   ├── youtube/                    # Shorts upload, quota constants
│   ├── ingest/                     # classify content category (folder) + format probe (frames → Gemini Flash)
│   ├── indexer/                    # Pegasus index lifecycle (create once, reuse, expire)
│   ├── moments/                    # moment-detection interface + Pegasus impl + Gemini impl
│   ├── rating/                     # optional Gemini-based rating/filter pass
│   ├── ffmpeg/                     # cut, reformat (blur-bg, webcam-split), caption burn-in
│   ├── captions/                   # optional caption-spec generation via Gemini
│   ├── workflow/                   # main per-source-video pipeline
│   ├── notifications/              # Discord webhook alerts (copied from upload-automation)
│   ├── utils/                      # shared helpers (retry_transient, ffmpeg runner)
│   └── health/                     # tiny 200-OK HTTP server for Railway
├── tests/                          # mirrors src/ layout
└── docs/
    ├── plan.md                     # phased plan (not yet written)
    └── model-bakeoff.md            # Pegasus vs Gemini comparison results (the research output)
```

_Update this section when top-level structure shifts. No subdirectory CLAUDE.md files exist yet._

## 4. Commands

| Task          | Command                            |
| ------------- | ---------------------------------- |
| Install deps  | `uv sync`                          |
| Run dev       | `uv run python -m src.main`        |
| Build (image) | `docker build -t yt-shorts .`      |
| Test          | `uv run pytest`                    |
| Lint / format | `uv run ruff check && uv run ruff format` |
| Type check    | `uv run mypy src`                  |
| DB migrate    | `uv run alembic upgrade head`      |
| Get OAuth token | `uv run python scripts/get_refresh_token.py` |
| FFmpeg sanity check | `ffmpeg -version` (inside container) |
| Deploy        | `git push` (Railway auto-deploys from main) |

## 5. Coding Rules

These are non-negotiable. When in doubt, follow these over personal preference.

### Style
- Match the surrounding code's style. Don't reformat unrelated lines.
- Naming: `snake_case` for variables/functions/modules, `PascalCase` for classes, `SCREAMING_SNAKE_CASE` for constants and env-derived values.
- Type hints everywhere. `mypy` runs in CI. No untyped public functions.
- No single-letter names except loop indices.
- Prefer clear names over comments. Comment _why_, not _what_.
- Pydantic models for all external boundaries (config, env vars, Pegasus/Gemini responses, moment specs, caption specs, format profiles).
- No placeholder values (`TODO`, `xxx`, fake API keys) in committed code. No commented-out code.

### Structure
- Functions do one thing. If a function needs an "and" in its name, split it.
- Keep files focused. If a file passes ~400 lines, consider splitting.
- No circular dependencies between modules.
- Layering: `workflow/` orchestrates and may depend on any leaf module. Leaf modules (`drive/`, `youtube/`, `ingest/`, `indexer/`, `moments/`, `rating/`, `ffmpeg/`, `captions/`, `db/`, `notifications/`) must not depend on each other or on `workflow/`.
- **Moment detection is a swappable interface.** `moments/` exposes one Protocol/ABC; `pegasus_impl.py` and `gemini_impl.py` both implement it. `workflow/` selects via config. Never reach into a specific impl from outside `moments/`.
- One async loop, one process. No threading or multiprocessing. FFmpeg is invoked as a subprocess and awaited.
- Don't introduce new patterns when an existing one already solves the problem.
- Don't add dependencies without flagging it. Justify each new package.

### Safety
- Validate all external input (Drive metadata, config, env vars, Pegasus/Gemini responses, FFmpeg probe output) at the boundary using Pydantic. Inner code assumes data is valid.
- Use parameterized queries — never string-concatenate SQL.
- **Never shell-concatenate FFmpeg arguments.** Always pass argv lists to `asyncio.create_subprocess_exec`. Filenames and timestamps from external sources must never reach a shell.
- Never log: OAuth tokens, refresh tokens, API keys (TwelveLabs, Gemini, Replicate, YouTube), Discord webhook URL, full request bodies. Log IDs and statuses only. Use the structured logger — never `print()`.
- Fail closed: if auth, quota check, config validation, or FFmpeg invocation errors, do not upload. Surface via Discord and mark the row `failed`.
- `drive_file_id` of the **source video** is the canonical idempotency key for the source. Each `Short` row has its own `(source_drive_file_id, moment_index)` unique key — last line of defence against double-uploaded shorts.

### Errors
- Don't swallow exceptions silently. Either handle them meaningfully or let them propagate to the per-source-video error handler in `workflow/processor.py`.
- The per-video pipeline has exactly one place that catches broad exceptions: the top of `process_source_video()`. That handler moves the source file to `failed/`, writes an `.error.txt`, updates DB, and pings Discord.
- Distinguish expected failures (quota exhausted, malformed config, transient API errors, FFmpeg non-zero exit on a known cause) from bugs (KeyError, AttributeError). Expected failures are typed; bugs propagate.
- Retries: exponential backoff with jitter for Pegasus, Gemini, YouTube, Drive transient errors (429, 5xx). Max 3 attempts unless documented otherwise.
- Never retry on 4xx auth/permission errors — surface immediately.
- **A failed moment detection ≠ pipeline failure.** If Pegasus/Gemini returns "no good moments found," that is a successful run with zero outputs. Log it, ping Discord (info-level), move source to a `no-shorts-found/` subfolder, mark DB row `completed_no_output`.

### Tests
- New behavior comes with a test. Bug fixes come with a regression test.
- Test names describe the behavior: `returns_empty_when_pegasus_finds_no_high_energy_moments`.
- External APIs (Drive, YouTube, Pegasus, Gemini, Replicate, Discord) are always mocked in tests. No live calls in CI.
- FFmpeg is invoked in tests against **tiny fixture videos** (a few seconds, ship them in `tests/fixtures/`). Do not mock FFmpeg itself — mocking the subprocess hides real bugs. Do mock the surrounding API calls.
- Time-dependent code uses an injectable clock; tests freeze it.
- Timezones: tests run in UTC; never call `datetime.now()` without a tz, never call `datetime.utcnow()` (deprecated).

## 6. Working Principles for AI Edits

When making changes, follow these in order:

1. **Think before coding.** State assumptions out loud. Moment-detection prompts, format-profile heuristics, and caption-spec schemas are areas where guessing is expensive — ask.
2. **Simplicity first.** Write the minimum code that solves the problem. No speculative abstractions. Specifically: do **not** build a transitions/montage system for shorts. Shorts are one continuous clip. That was a long-form-editor idea that doesn't belong here.
3. **Surgical changes.** Edit only what the task requires.
4. **Match existing style.** Read nearby code first.
5. **Define done.** State how success will be verified — which test, which command, which Drive→Shorts→YouTube end-to-end behavior is observable.
6. **No silent scope creep.** If you find a separate problem, surface it. Don't fix it inline.
7. **Version discipline.** v0 is research + bake-off. **Do not commit to a model choice (Pegasus vs Gemini) in code until `docs/model-bakeoff.md` exists with results on ≥5 real source videos.** Build behind the swappable `moments/` interface from day one.
8. **Cost discipline.** Pegasus indexing is the single biggest cost lever. Any code path that re-indexes a video that already has a live index is a bug. The cost model assumes ~$5/hour of source video as the ceiling; flag any change that risks exceeding it. **Every priced operation must emit a `CostEvent` (see Section 8) — adding a new external API call without wiring up cost tracking is incomplete work, not "we'll add it later."**

## 7. Definition of Done

A task is done when:
- [ ] The change is minimal and matches existing style.
- [ ] All tests pass locally (`uv run pytest`).
- [ ] Lint and type checks pass (`uv run ruff check && uv run mypy src`).
- [ ] New behavior has a test.
- [ ] No new warnings introduced.
- [ ] No secrets, tokens, or PII in logs or commits.
- [ ] Temp files (downloaded source videos, intermediate FFmpeg renders, extracted frames) are cleaned up on every code path including errors.
- [ ] If the change touches the per-video pipeline, the failure path (source moved to `failed/`, `.error.txt`, DB update, Discord ping) still works.
- [ ] If the change touches moment detection, both `moments/` impls still satisfy the interface (or the test for the unaffected one is updated explicitly).
- [ ] If the change adds or modifies a priced external call, a `CostEvent` is written for it and `costs/pricing.py` is up to date.
- [ ] The commit message explains the _why_, not just the _what_.

---

## 8. Project-Specific Domain Rules

These encode decisions from planning. Don't violate them without explicit operator approval.

### Folder model
- Drive structure: `<shorts-root>/to-process/<category>/`, `<shorts-root>/processed/<category>/`, `<shorts-root>/failed/<category>/`, `<shorts-root>/no-shorts-found/<category>/`.
- **Content category comes from the parent folder name** (`gameplay`, `tutorial`, `podcast`, etc.). New category = new subfolder. Unknown folder names default to `generic` with a Discord warning.
- This is separate from the upload-automation Drive root. Do not share folders.

### Ingest and classification
- Two independent classifications happen at ingest:
  1. **Content category** — read from the folder name. Free, deterministic.
  2. **Format profile** — sample 5 FFmpeg frames (start, 25%, 50%, 75%, end) and send to Gemini 2.5 Flash with a JSON-schema response: `{has_facecam, facecam_position, facecam_size_pct, source_aspect_ratio, is_already_vertical}`.
- Format profile is cached per source video; never re-probe.
- If facecam detection disagrees across the 5 frames (e.g. 3/5 vs 2/5), flag as `facecam_unstable` and fall back to the default format for that category. Do not silently average.

### Moment detection
- Pegasus indexes a video **once**. The index ID is persisted in `MomentDetectionRun.index_id`. Any code path that would re-index a video with a live index must check the DB first.
- **Confirm with TwelveLabs' actual API** that an index can be queried for multiple downstream tasks (shorts moments today; potentially long-form edit segments later). If indexes are scoped per-task or expire fast, the shared-index optimization does not apply and the cost model needs revisiting.
- Moments are bounded: each ≤180 seconds, no overlapping moments, at least 5 seconds gap between moments from the same source.
- A run returning 0 moments is a **valid result**, not a failure. See error rules above.

### Format selection (per short)
- Decision table (category × format_profile → FFmpeg recipe):
  - `gameplay` + no facecam → blur-top/bottom, gameplay centered
  - `gameplay` + facecam → webcam top, gameplay bottom (split ratio from config, default 30/70)
  - `tutorial` / `podcast` + facecam → same webcam-above-content split
  - anything `is_already_vertical=true` → straight pass-through (still re-encode to YouTube Shorts spec)
  - everything else → blur-top/bottom default
- Split ratios accepted: 25/75, 30/70, 35/65. Reject other values at config-load.

### Captions (optional)
- Only runs if `config.captions.enabled = true` for the category.
- Caption spec from Gemini is `{text, start_seconds, end_seconds, position}` per beat. Validate with Pydantic; reject any spec where `end_seconds > short_duration` or `start_seconds >= end_seconds`.
- Burn-in via FFmpeg `drawtext`. No SRT sidecar — YouTube Shorts uploads don't carry them through cleanly for our use case.

### Idempotency and recovery
- Source-video `drive_file_id` is UNIQUE in `source_videos`.
- Each `Short` row has UNIQUE `(source_drive_file_id, moment_index)`.
- On startup, rows stuck in `processing` are reset to `pending`. The poll cycle re-queues `pending` rows.
- `youtube_video_id` is saved to the `Short` row immediately after upload. On retry of a partially-completed source, shorts that already have a `youtube_video_id` are skipped.
- If the indexer succeeded but cut/upload failed, the Pegasus index ID is preserved and reused on retry. Do not re-index.

### Quotas and cost
- YouTube Data API: track unit usage per UTC day. Warn at 80%, hard-stop at 100%. Each Shorts upload costs 1,600 units. Default 10,000 units/day → ~6 uploads/day max. **If a source produces more shorts than remaining quota, queue the overflow for the next UTC day** rather than dropping them.
- Gemini: handle 429s with exponential backoff. Pause new pickups for 60s if consistently rate-limited.
- Replicate: only if used; failures surface via Discord.

### Per-step cost tracking (mandatory)
Every priced operation writes one `CostEvent` row immediately after the operation succeeds. No batched writes, no end-of-pipeline reconciliation — if the step ran, the cost row exists. This is how we answer "was Pegasus actually worth $4?" with data instead of guesses.

- **`CostEvent` schema:** `id, source_video_id, short_id (nullable), step, provider, model, units (e.g. minutes, tokens, images, api_units), unit_cost_usd, total_cost_usd, metadata_json, created_at`.
- **Priced steps that must emit a `CostEvent`:**
  - `ingest.format_probe` — Gemini Flash, 5 frames in, JSON out. Units = input + output tokens.
  - `indexer.pegasus_index` — Pegasus index creation. Units = source video minutes.
  - `moments.pegasus_query` — Pegasus moment-detection query. Units = per-query (confirm with TwelveLabs API).
  - `moments.gemini_query` — Gemini Flash moment detection. Units = input tokens (incl. video) + output tokens.
  - `rating.gemini_rate` — optional rating pass. One event per short rated.
  - `captions.gemini_spec` — optional caption-spec generation. One event per short.
  - `youtube.upload` — Shorts upload. Units = API units consumed (1,600 per upload).
  - Any future priced step gets added here **and** gets a corresponding entry in `costs/`.
- **Source of truth for prices:** `costs/pricing.py` holds a versioned dict of current per-unit costs. Bump the version when a provider changes pricing — never silently edit. `CostEvent.metadata_json` records the pricing version used.
- **Estimates vs. actuals:** `total_cost_usd` is always best-effort at write time. If a provider returns actual billed cost later (some APIs do), reconcile via a separate `actual_cost_usd` column rather than overwriting the estimate.
- **Per-source rollup:** `MomentDetectionRun.cost_estimate_usd` and a `SourceVideo.total_cost_usd` are computed views/sums over `CostEvent`, not independently written. One source of truth.
- **Alerts:** Discord-warn if any single source crosses **$5 total**. Hard-stop (don't start more steps for that source) at **$8** and ping Discord at error level — this is a runaway-cost circuit breaker, tune the threshold once we have real numbers.
- **Logging:** every priced step logs `step=<name> provider=<x> units=<n> cost_usd=<y>` at info level. Never log API keys; cost numbers and unit counts are fine.

### Notifications
- Errors and warnings always go to Discord. Success pings gated by `DISCORD_SUCCESS_PINGS=true` (default false).
- Per-source summary ping: "Source X → N shorts uploaded, M failed, $Y spent (breakdown: index $a, moments $b, captions $c, upload $d)." Numbers come from summed `CostEvent` rows for that source, never recomputed.

### Resource discipline (Railway $5 plan)
- 8 GB RAM, ephemeral disk. **Source videos can be large (1h+).** Always stream from Drive to disk, never load into memory. Stream from disk into FFmpeg.
- Delete the source file from local disk as soon as the Pegasus index is confirmed live and FFmpeg has finished all cuts for that source. Pegasus has its own copy; YouTube has the uploaded shorts.
- Intermediate render files (per-short MP4s) are deleted after upload succeeds.
- Never bake credentials into the Docker image. Env vars only.

---

## 9. Lessons Learned

> Recurring problems and the fixes that worked. Append here (format: date, one-line cause, **Rule:**) whenever a bug or friction point shows up more than once.

_(Empty — project hasn't shipped yet. First entries will likely come from: Pegasus index lifecycle surprises, FFmpeg filter-graph quoting on Railway's image, YouTube's Shorts classification rules.)_
