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
| v2 Phase 8 | Gemini receives text descriptions only | **Gemini also receives the source video** via Files API | Text descriptions alone caused Gemini to select on summaries rather than verifying visual quality. Providing the video lets Gemini confirm timestamps visually and judge excitement from actual frames. The local file is now kept alive until after Gemini select, then deleted. |
| v2 Phase 8 | Timestamps snapped to 15-second window boundaries | **Free timestamp selection** — Gemini picks any second | 15-second grid produced clips starting/ending at arbitrary points mid-action. Gemini now sets `start_seconds`/`end_seconds` to the precise second the action begins or the round concludes. |
| v2 Phase 8 | No title generation | **Gemini generates a YouTube Shorts title per clip** | Operator requested titles following best practices: 40–70 chars, front-loaded hook, 1–2 emojis, curiosity-gap pattern, no hashtags in title. |
| v2 Phase 10 | Discord bot buttons for approve/reject | **Emoji reactions (👍/👎)** | Bot buttons require an interaction endpoint (webhook server). Emoji reactions only need a bot token to poll — simpler, no server changes. Bot pre-adds both reactions; operator clicks one. |
| v2 Phase 11 | `#Shorts` in video title | **Hashtags only in description** | YouTube uses `#Shorts` as a discovery chip in the description; putting it in the title wastes the character budget and looks unprofessional. |

---

## v2 — Cut, Reformat, Upload ✅ Complete

End state: a long-form video dropped into Drive comes out as one or more YouTube Shorts, uploaded and live, with every step cost-tracked. Clip selection is a two-step process: Pegasus 1.5 describes the video in 15-second windows; Gemini 2.5 reads the descriptions + the source video and decides which windows to use, merging consecutive windows freely into clips of whatever length serves the content best. Clip duration is no longer fixed — Gemini decides per-clip based on what the content actually warrants and where the action naturally concludes.

**In scope:** `Short` DB model with per-short cost attribution, replacement of Marengo with the Pegasus-describe + Gemini-select pipeline, FFmpeg cutting and 9:16 reformatting, human approval gate (Discord emoji reactions), YouTube Shorts upload with quota tracking, Gemini-generated titles.

**Out of scope:** highlight-reel pipeline, captions/text overlays, automated rating pass beyond Gemini's own selection.

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

### Phase 9 — FFmpeg Cut + Reformat ✅

`ffmpeg/recipes.py` — `CutRecipe` dataclass + `select_recipe(category, profile, settings)`. Three recipes:
- **blur-bg** — gameplay zoomed/center-cropped into content zone (configurable `center_pct`, default 70%), blurred background behind it
- **split** — facecam cropped to top `facecam_top_pct` (default 30%), gameplay center-cropped to remaining 70%; `vstack=inputs=2`; no blur
- **passthrough** — `scale`+`pad` to 1080×1920 for already-vertical source

`ffmpeg/cut.py` — `cut_short(source_path, output_path, start_seconds, end_seconds, recipe, info)`. Re-encode (not stream-copy). argv lists only, no shell strings. Output: 1080×1920 H.264/AAC, `-crf 23 -preset fast`, `-movflags +faststart`.

### Phase 10 — Discord Approval Gate ✅

**New DB model: `ApprovalRequest`** — `(id, source_video_id FK, short_id FK UNIQUE, discord_message_id, drive_preview_file_id, decision nullable, decided_at nullable, expires_at, created_at)`.

Flow:
1. Each cut MP4 uploads to `<drive-root>/pending-approval/<category>/` with public sharing
2. One Discord message per clip: rank, duration, Drive preview link, rationale, react 👍/👎
3. Bot pre-adds both reactions; operator clicks one; bot polls each tick
4. Approved → Phase 11; rejected → Drive preview deleted, Short marked rejected
5. 48h timeout → auto-rejected

`approval/discord_approval.py` — plain aiohttp REST calls against Discord API v10 (no discord.py). `approval/gate.py` — `submit_for_approval`, `check_pending_approvals`, `_apply_decision`, `upload_approved_shorts`.

### Phase 11 — YouTube Shorts Upload ✅

`youtube/upload.py` — `upload_short(local_path, title, category, rationale, settings)`. `videos.insert` with resumable `MediaFileUpload`. Hashtags in description only (first 3 become YouTube chips), never in title.

`youtube/quota.py` — `check_and_reserve_upload_quota(session)`. Tracks daily unit usage in `QuotaUsage` table. Raises `QuotaExhausted` at 10,000 units (6 uploads/day). Discord warning at 8,000 (80%). Overflow stays `approved` and is picked up next UTC day.

---

## v2 Lessons

- **Gemini + text only → over-conservative selection.** When Gemini received only the text window descriptions, it produced fewer, lower-quality clips. Adding the source video restored quality. Prompt must frame the video as a "visual aid to confirm timestamps" not a second quality gate — otherwise Gemini becomes more selective, not more accurate.
- **15-second window boundaries as timestamps → wrong cut points.** Clips were starting and ending mid-action because Gemini snapped to the segment grid. Free timestamp selection fixed this; prompting for natural endpoints (all enemies dead, ace announcement, round-win, spike detonation) produces clean endings.
- **Constraint name in Alembic migration must match explicit name from prior migration.** The Phase 8 migration tried to drop `"shorts_moment_id_key"` (Postgres auto-name) but the constraint was created with explicit name `"uq_shorts_moment_id"`. Always use the explicit name from the `CREATE` statement, not the Postgres default.
- **`CreateAnalyzeTaskResponse.task_id`, not `.id`.** TwelveLabs SDK returns `task_id` on the analyze task response; `.id` does not exist. The `# type: ignore` comment masked the issue at type-check time.

---

## v3 — Quality, Reach & Operational Control

**Goal:** Given a working end-to-end pipeline, make the output better, add new content types, and give the operator more control without touching the core pipeline.

**In scope:** canary upload mode, prompt replay tooling, caption burn-in, tutorial/podcast category prompts, highlight reel pipeline from existing `SegmentDescription` rows.

**Out of scope:** multi-account YouTube upload, automated thumbnail generation, A/B testing different Gemini prompts.

### Phase 12 — Canary Upload Mode

First Short uploaded each UTC day goes up as `unlisted`; operator gets a Discord ping with the YouTube URL. If it looks good, subsequent Shorts for that day go `public` automatically. If it looks wrong, operator can reject from Discord before anything goes public.

`CANARY_UNLISTED_FIRST_OF_DAY` env var (default `true`). `QuotaUsage` table already tracks daily uploads — use its `created_at` to detect first-of-day. `upload_approved_shorts` in `gate.py` checks whether any Short was uploaded today; if not, sets `privacyStatus="unlisted"` and sends the canary ping.

**Done when:** the first upload each UTC day is unlisted and a Discord message with the YouTube URL is sent; subsequent uploads that day are public; setting `CANARY_UNLISTED_FIRST_OF_DAY=false` makes everything public.

### Phase 13 — Prompt Replay Tooling

Re-run Gemini clip selection against existing `SegmentDescription` rows without re-calling Pegasus (and without paying ~$1.00 again). Useful for prompt tuning after real-video feedback.

`scripts/replay_gemini.py` — CLI: `uv run python scripts/replay_gemini.py --source-id <id> [--prompt prompts/gemini/gameplay.txt]`. Reads `SegmentDescription` rows from DB for that source, re-downloads the source video from Drive (it's still in `processed/`), calls `select_clips`, and writes new `ClipSelection` + `Short` rows tagged with a `prompt_version` suffix so they don't collide with the original run.

**Done when:** running the script against a processed source produces new `Short` rows with the updated prompt, skips the Pegasus describe call entirely, and logs the Gemini cost for the replay.

### Phase 14 — Caption Burn-in (Optional, Per-Category)

Captions drawn by Gemini per clip, burned in via FFmpeg `drawtext`. No SRT sidecar — YouTube Shorts doesn't carry them cleanly.

`CAPTIONS_ENABLED_GAMEPLAY=true` env var gates the feature per category.

Gemini generates a caption spec: `[{text, start_seconds, end_seconds, position}]` per beat. Pydantic validates: `end_seconds ≤ clip_duration`, `start_seconds < end_seconds`, position one of `top|center|bottom`. Reject invalid specs entirely — don't partially apply.

`ffmpeg/captions.py` — builds `drawtext` filter chain. Font, size, and color are config values.

New `CostEvent` step: `captions.gemini_spec`. One event per Short if captions run.

**Done when:** a gameplay Short with captions enabled has burned-in text at the correct times; a Short without captions enabled is unchanged; an invalid spec is rejected and the Short is cut without captions (not failed).

### Phase 15 — Tutorial and Podcast Category Prompts

Write `prompts/pegasus/tutorial.txt`, `prompts/pegasus/podcast.txt`, `prompts/gemini/tutorial.txt`, `prompts/gemini/podcast.txt`.

Tutorial selection priorities: step-by-step demonstrations, "before and after" reveals, moments where the presenter explains a non-obvious trick. Avoid: intro/outro, restatements of agenda, filler.

Podcast selection priorities: emphatic delivery, surprising claims, topic pivots, quotable one-liners. Avoid: crosstalk, "let me find that link", sponsor reads, flat agreement.

Both prompts still use the same pipeline (Pegasus describe → Gemini select). Only the selection criteria change.

**Done when:** a tutorial video dropped into `to-process/tutorial/` produces clips that reflect tutorial-specific criteria; a podcast video produces clips around emphatic moments; gameplay prompts are unchanged.

### Phase 16 — Highlight Reel Export

`SegmentDescription` rows are already in the DB for every processed video. Use them to produce a timestamped highlight-reel JSON that a video editor (or future editor service) can consume — without re-calling Pegasus.

`scripts/export_highlight_reel.py` — CLI: `uv run python scripts/export_highlight_reel.py --source-id <id>`. Reads `SegmentDescription` rows, calls Gemini with a "highlight reel editor" prompt that returns `[{start_seconds, end_seconds, label, notes}]` ordered by narrative flow rather than excitement. Writes to `<source-name>_highlight_reel.json` locally and optionally uploads to Drive.

This is a read-only export — no new Short rows, no YouTube upload, no approval gate. Pure data product for the operator.

**Done when:** running the script against a processed source produces a JSON file with timestamped segments and labels; the Pegasus describe cost is not re-incurred; the JSON is human-readable and editable.

---

## v3 Risks

- **Canary mode relies on `QuotaUsage.created_at` for first-of-day detection.** If the DB is cleared or quota rows are missing, every upload would be treated as the first of the day. Guard with an explicit `uploaded_today` count query instead.
- **Prompt replay produces duplicate Shorts.** Tag replayed rows clearly with prompt version; add a `--dry-run` flag that prints what would be created without writing to DB.
- **Caption spec validation.** An LLM-generated spec with `end_seconds > clip_duration` would produce malformed FFmpeg filters. Pydantic boundary check at parse time, not at render time.
- **Tutorial/podcast describe prompts need real-video testing.** Don't ship prompts that were only designed at a desk — test against actual tutorial/podcast source videos before treating the output as reliable.
