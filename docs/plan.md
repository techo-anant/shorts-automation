# Shorts Automation — Build Plan

## v1 (Complete)

Drop a long-form video into a Drive subfolder → pipeline detects moments, logs everything to DB, and sends a Discord summary. No FFmpeg cutting or YouTube upload yet.

### Phase 1 — Scaffold
Async polling loop, health-check server (Railway), Pydantic config, SQLAlchemy models (SourceVideo, FormatProfile, MomentDetectionRun, Moment, CostEvent, QuotaUsage), Alembic migrations, Discord notifier, project structure.

### Phase 2 — Drive Round-Trip + Cost Tracking
DriveClient wrapping Google Drive API v3 (list, download, move, get_or_create_folder). Workflow processor with per-source-video pipeline: download → probe → index → detect → move to processed/ or no-shorts-found/. Cost circuit breaker (warn at $5, hard-stop at $8). `CostEvent` write immediately after every priced step.

### Phase 3 — Gemini Format Probe
FFprobe wrapper (VideoInfo). Frame extractor (5 samples at 0/25/50/75/100%). Gemini 2.5 Flash per-frame JSON classification: `has_facecam`, `facecam_position`, `source_aspect_ratio`, `is_already_vertical`. Threshold: ≥4/5 frames must agree on facecam; otherwise `facecam_unstable=True`. Prompt explicitly excludes in-game character art (e.g. Valorant agent portraits, HUD faces) — only real human webcam overlays qualify.

### Phase 4 — Marengo Indexing + Moment Detection
Idempotency-safe Marengo index lifecycle (check DB before touching API; reuse if index already ready). Two concurrent semantic search queries per video: `shorts` (high-energy clips ≤180s) and `highlight_reel` (key moments, no duration cap). Clip constraint enforcement: drop <5s clips, drop overlapping clips, enforce ≥5s gap between accepted clips. Results written as `Moment` rows with `clip_type` column. Raw API response stored on `MomentDetectionRun` for bake-off analysis. Discord summary with moment counts and cost breakdown.

---

## Why We Moved from Marengo to Pegasus (mid-v2)

This is the most important architectural decision in v2 and worth recording prominently so future work doesn't re-litigate it.

**The original v1 choice (Marengo over Pegasus) was made on cost grounds** — Marengo at ~$0.042/min indexing + $0.004/query was roughly half the price of Pegasus, and the ability to query one index multiple times made it a clean fit for the dual shorts + highlight-reel use case. On paper, Marengo was the right call.

**In practice, Marengo's clip selection failed on real videos.** Three specific problems surfaced once real gameplay videos went through:

1. **Marengo couldn't produce clips of useful length.** It returned hundreds of 5–8 second segments — essentially every 6-second window of the video, ranked by similarity to the query. With a 60-second minimum, zero shorts came back from a 33-minute video. With a 5-second minimum, hundreds of un-shippable micro-clips came back. There was no middle ground.

2. **Marengo selected on visual features the operator didn't want.** On a Valorant gameplay video, the top-ranked "exciting moment" was the Valorant logo intro screen — high visual similarity to "Valorant", low actual interest. Marengo is an embedding-based search; it scores on what it can vectorize, not on what's actually clip-worthy.

3. **Marengo provides no way to express intent.** The operator can't say "find me a sequence with multiple kills in a round" — Marengo accepts a text query and returns similar-looking video segments. It's a *search* model, not a *reasoning* model.

**Pegasus 1.5 is a video-language model**, not a search engine. It accepts natural-language prompts, describes audio + visuals + on-screen action, and reasons over the full video. Combined with Gemini reading those descriptions, the pipeline gets:

- **Flexible clip length** — Gemini merges consecutive 15-second windows into clips of whatever length the content warrants. 12s, 45s, 90s — all fine. No fixed minimum, no fixed maximum (besides YouTube's 180s hard cap).
- **Intent-driven selection** — the category prompt can specify "a sequence with multiple kills in a round" or "a tonal shift in the conversation" or anything else. Pegasus and Gemini both reason about meaning, not pixel similarity.
- **Auditable rationale** — every clip selection comes with a one-sentence "why this clip" from Gemini, stored in the DB and shown in the Discord approval message. The operator can sanity-check the model's reasoning, not just its picks.

**The cost trade is real but acceptable.** For a 33-minute video the Pegasus describe call runs ~$1.00 (33 min × $0.0292/min + ~5k output tokens × $0.0075/1k) vs. Marengo's ~$1.41 total — roughly comparable, with the critical difference being Pegasus is producing usable output and Marengo wasn't.

**What we keep from the Marengo era:** the `VideoIndex` table abstraction from Phase 6 still works — Pegasus indexes video too, the idempotency story is unchanged. The cost-tracking discipline (one `CostEvent` per priced step) becomes even more important now that per-video cost is non-trivial.

---

## Deviations from Original Plan

| Phase | Original plan | What actually shipped | Reason |
|---|---|---|---|
| v1 | TwelveLabs **Pegasus** as primary model | **Marengo 3.0** used instead | Marengo was ~2× cheaper ($0.042/min index + $0.004/query vs Pegasus ~$0.029/min/call). Could be queried multiple times against one index. Pegasus stub kept. |
| v1 | Single query per video | Two queries (`shorts` + `highlight_reel`) run concurrently | Enabled both YouTube Shorts output and long-form highlight timestamps from one index; negligible extra cost ($0.008 per video for both queries). |
| v2 (mid) | Continue with Marengo for clip selection | **Reverted to Pegasus 1.5** — Marengo torn out | Marengo's clip selection failed in practice on real gameplay videos: returned hundreds of 5–8 second segments scoring on visual features the operator didn't want (e.g. the Valorant logo intro), couldn't reliably produce clips ≥30 seconds, and had no way to express *what kind* of moment to find. Pegasus 1.5 is a video-language model that accepts natural-language prompts, describes audio + visuals, and reasons over the full video. New design: **one Pegasus call describes the video in 15-second windows; Gemini 2.5 reads the descriptions plus the source video and selects/merges windows into clips per category-specific instructions.** |

---

## v2 — Cut, Reformat, Upload

End state: a long-form video dropped into Drive comes out as one or more YouTube Shorts, uploaded and live, with every step cost-tracked. Clip selection is a two-step process: Pegasus 1.5 describes the video in 15-second windows; Gemini 2.5 reads the descriptions + the source video and decides which windows to use, merging consecutive windows freely into clips of whatever length serves the content best. Clip duration is no longer fixed — Gemini decides per-clip based on what the content actually warrants.

**In scope:** `Short` DB model with per-short cost attribution, replacement of Marengo with the Pegasus-segment-describe + Gemini-clip-select pipeline, FFmpeg cutting and 9:16 reformatting, human approval gate (Discord), YouTube Shorts upload, surfacing the Pegasus index ID as a long-lived asset.

**Out of scope:** highlight-reel pipeline output (data model stays, no pipeline consumes it in v2), captions/text overlays, automated rating pass beyond Gemini's own selection.

### Phase 5 — Test Backfill for v1 ✅

Tests for the four v1 modules with highest "silent wrong number" risk: `costs/`, `ingest/format_probe.py`, `indexer/` idempotency, `moments/` constraint enforcement. External APIs mocked; FFmpeg against tiny fixture videos; injectable clock; UTC-only.

### Phase 6 — VideoIndex as Long-Lived Asset ✅

`VideoIndex` model: `(id, source_video_id FK, provider, index_id, status, expires_at nullable, created_at)`. One row per source video per provider. Idempotency check queries `VideoIndex` directly. Inline expiry check before every query; Discord warning at T-7 days; background sweep in polling loop. Alembic migration backfills from existing `MomentDetectionRun` rows.

### Phase 7 — `Short` DB Model + Per-Short Cost Attribution ✅

`Short` model tracking per-clip pipeline state (pending → cutting → cut → pending_approval → approved → uploading → uploaded, plus failed/rejected/skipped). `clip_selection_id` FK added in Phase 8 when `ClipSelection` table is created; for now, `moment_id` bridges. `gemini_rationale` and `gemini_rank` columns added in Phase 8 migration. `CostEvent.short_id` FK wired. Startup recovery resets cutting/uploading Shorts to pending.

### Phase 8 — Pegasus Describe + Gemini Clip Selection

This is the new clip-selection brain. Marengo is torn out completely; Pegasus 1.5 plus Gemini 2.5 replace it. Two stages per video:

**Stage 1 — Pegasus describes the whole video in 15-second windows (one call).**

One Pegasus call per video with a category-configurable prompt:

> *"Describe this video in 15-second windows from start to finish. For each window, return the start time, end time, and a one-to-two-sentence description of what is happening — including visuals, on-screen action, dialogue or narration audio, and any notable sound effects. Be specific. Return strictly JSON: a list of `{start_seconds, end_seconds, description}` objects."*

Prompt templates live in `prompts/pegasus/<category>.txt`. Gameplay emphasizes round structure, kills, callouts, clutch moments; podcast emphasizes topic transitions and emphatic delivery; tutorial emphasizes step boundaries and demonstrations.

**New DB model: `SegmentDescription`** — `(id, source_video_id FK, start_seconds, end_seconds, description TEXT, created_at)`. One row per 15-second window. A 33-minute video produces ~132 rows. Stored in full so Gemini, the editor service, and debugging have descriptions without re-calling Pegasus.

CostEvent: provider=pegasus, units=video minutes (input) + text tokens (output).

**Stage 2 — Gemini reads descriptions + source video and selects clips.**

Gemini 2.5 receives: source video file (Files API), full `SegmentDescription` list, category-specific selection prompt, `FormatProfile`. Gemini:
- Identifies clips that would perform well as YouTube Shorts
- **Freely merges consecutive segments** into clips of any length (no fixed min/max besides 180s hard cap)
- **Ranks each clip 1–10** (10 = exceptional, 1 = weakest pick)
- Returns strict JSON: `{start_seconds, end_seconds, rationale, rank, source_segment_ids}`
- Returns empty list + `no_clips_reason` if nothing is clip-worthy

**New DB model: `ClipSelection`** — `(id, source_video_id FK, start_seconds, end_seconds, rationale TEXT, rank INTEGER, source_segment_ids JSONB, gemini_run_id FK, created_at)`. Each row becomes one `Short`.

**New DB model: `GeminiRun`** — `(id, source_video_id FK, prompt_version, request_body JSONB, response_body JSONB, status, error_class nullable, error_message nullable, created_at)`. Full request/response stored for debugging and prompt tuning.

**`Short` schema update (in this phase's migration):** drop `moment_id`, add `clip_selection_id FK` + `gemini_rationale TEXT` + `gemini_rank INTEGER`.

**Validation before creating `ClipSelection` rows:**
- `start_seconds < end_seconds`
- `end_seconds <= source video duration`
- No two clips overlap (drop lower-rank one)
- Minimum 3 seconds (sanity guard)
- Maximum 180 seconds (trim from end + log warning)
- Drop clips entirely within first/last 5 seconds of source
- `rank` integer in [1, 10]; clamp out-of-range values

CostEvent for the Gemini call.

**Tearing out Marengo:** `moments/marengo_impl.py`, `moments/pegasus_impl.py` (old stub), `MomentDetectionRun` Marengo references, Marengo pricing entries. `VideoIndex` stays — Pegasus uses it identically.

**Done when:** one real source video produces ~132 `SegmentDescription` rows, populated `ClipSelection` rows with varied durations and ranks, one `Short` per selection in `pending` status with `gemini_rank` and `gemini_rationale` denormalized in, and cost events for both calls written with realistic numbers.

### Phase 9 — FFmpeg Cut + Reformat

Two concerns kept separate: cutting (timestamp → MP4 of that range) and reformatting (landscape → 9:16).

`ffmpeg/cut.py` — `cut_clip(source_path, start_seconds, end_seconds, dest_path)`. Re-encode (not stream-copy) for keyframe alignment. argv lists only, no shell strings. `-ss` after `-i` for frame accuracy.

`ffmpeg/reformat.py` — picks a recipe from the decision table in `CLAUDE.md` Section 8:
- `gameplay` + no facecam → blur-top/bottom
- `gameplay` + facecam → webcam top, gameplay bottom (default 30/70 split)
- `tutorial`/`podcast` + facecam → same webcam-above split
- `is_already_vertical=true` → straight re-encode to YouTube Shorts spec
- `facecam_unstable=true` → fall back to no-facecam recipe, Discord info ping

Encoder defaults (every recipe specifies explicitly): `-pix_fmt yuv420p`, `-c:v libx264 -preset medium -crf 20`, `-c:a aac -b:a 128k -ar 48000`, `-movflags +faststart`, `-r 30`.

Wire into workflow: for each `Short` in `pending`, cut → reformat → save → set `local_path`, status `cut`.

**Golden-file fixture tests in `tests/fixtures/`:** one ~5-second video per format profile. FFprobe asserts on output: resolution exactly 1080×1920, duration ±0.05s, audio stream present, frame 0 not green.

### Phase 10 — Discord Approval Gate

Human approval between cut and upload. Always on.

**New DB model: `ApprovalRequest`** — `(id, source_video_id FK, short_id FK UNIQUE, discord_message_id, drive_preview_file_id, decision nullable, decided_at nullable, expires_at, created_at)`.

Flow:
1. Phase 9 finishes → status `cut` → `pending_approval`
2. Upload local MP4 to `<drive-root>/pending-approval/<source-name>/`; set sharing to "anyone with link"
3. After all shorts for source are in `pending_approval`, post Discord message listing all clips **sorted by `gemini_rank` descending** — each entry shows: duration, Drive preview link, `Rank N/10`, rationale, approval affordance
4. Operator approves/rejects; decision written to `ApprovalRequest` + `Short` row
5. Approved → Phase 11; rejected → delete local file + Drive preview + tag CostEvent

**Timeout (48h default to rejected):** background task every 15 min checks `decision IS NULL AND expires_at < now()`.

### Phase 11 — YouTube Shorts Upload

`youtube/client.py` — `upload_short(local_path, title) -> youtube_video_id`. `videos.insert` with `#Shorts` in title, vertical spec, category from source category. Only `approved` Shorts reach this phase.

Quota: 1,600 units/upload; hard-stop at exhaustion; overflow stays `approved` with `local_path` set, picked up next UTC day.

**Daily canary:** first short each UTC day uploaded as `unlisted`; operator gets Discord ping + URL. Subsequent shorts go public. Toggleable via `CANARY_UNLISTED_FIRST_OF_DAY` env var (default true).

After upload: write `youtube_video_id` immediately, delete Drive preview, move source to `uploaded/<category>/`, delete local cut files.

### Phase 12 — External API Response Persistence

Every external API call goes through `await call_external(provider, run_row, request_fn)` — runs the request, persists the response to the run row (success or failure), re-raises exceptions. On 4xx/5xx, response body and headers land in the DB before the exception propagates.

---

## v2 Risks

- **Pegasus describe cost.** ~$1.00 per 33-minute video (33 × $0.0292 + ~5k output tokens × $0.0075). Scales linearly on video length.
- **Gemini clip-selection quality.** Manually review first 5–10 videos' clip selections and tune category prompts. Treat prompt files as versioned code.
- **FFmpeg recipe correctness.** Real-video verification matters more than fixtures — a green test on a 5-second clip doesn't catch off-by-one crops.
- **YouTube quota math.** 10,000 units/day ÷ 1,600 per upload = 6 shorts/day max. Overflow path must work first time.
- **Facecam position drift.** Format probe captures position at ingest; mid-video drift is out of scope.
- **Approval gate as bottleneck.** 48h timeout caps individual shorts; total volume isn't capped.
- **Marengo teardown migration.** Don't delete v1 data — it's historical record.

---

## v3 Preview (not commitment)

After v2 ships: structured observability (correlation IDs, file-state logging), backfill/replay tooling (re-run Gemini selection against existing `SegmentDescription` rows without re-calling Pegasus), Gemini-generated titles, editor service skeleton consuming `SegmentDescription` for highlight reels.
