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

- **Flexible clip length** — Gemini merges consecutive 10-second windows into clips of whatever length the content warrants. 12s, 45s, 90s — all fine. No fixed minimum, no fixed maximum (besides YouTube's 180s hard cap).
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
| v2 (mid) | Continue with Marengo for clip selection | **Reverted to Pegasus 1.5** — Marengo torn out | Marengo's clip selection failed in practice on real gameplay videos: returned hundreds of 5–8 second segments scoring on visual features the operator didn't want (e.g. the Valorant logo intro), couldn't reliably produce clips ≥30 seconds, and had no way to express *what kind* of moment to find. Pegasus 1.5 is a video-language model that accepts natural-language prompts, describes audio + visuals, and reasons over the full video. New design: **one Pegasus call describes the video in 10-second windows; Gemini 2.5 reads the descriptions and selects/merges windows into clips per category-specific instructions.** |
| v2 Phase 8 | Timestamps snapped to 10-second window boundaries | **Free timestamp selection** — Gemini picks any second | 10-second grid produced clips starting/ending at arbitrary points mid-action. Gemini now sets `start_seconds`/`end_seconds` to the precise second the action begins or the round concludes. |
| v2 Phase 8 | No title generation | **Gemini generates a YouTube Shorts title per clip** | Operator requested titles following best practices: 40–70 chars, front-loaded hook, 1–2 emojis, curiosity-gap pattern, no hashtags in title. |
| v2 Phase 10 | Discord bot buttons for approve/reject | **Emoji reactions (👍/👎)** | Bot buttons require an interaction endpoint (webhook server). Emoji reactions only need a bot token to poll — simpler, no server changes. Bot pre-adds both reactions; operator clicks one. |
| v2 Phase 11 | `#Shorts` in video title | **Hashtags only in description** | YouTube uses `#Shorts` as a discovery chip in the description; putting it in the title wastes the character budget and looks unprofessional. |

---

## v2 — Cut, Reformat, Upload ✅ Complete

End state: a long-form video dropped into Drive comes out as one or more YouTube Shorts, uploaded and live, with every step cost-tracked. Clip selection is a two-step process: Pegasus 1.5 describes the video in 10-second windows; Gemini 2.5 reads the descriptions and decides which windows to use, merging consecutive windows freely into clips of whatever length serves the content best.

**In scope:** `Short` DB model with per-short cost attribution, replacement of Marengo with the Pegasus-describe + Gemini-select pipeline, FFmpeg cutting and 9:16 reformatting, human approval gate (Discord emoji reactions), YouTube Shorts upload with quota tracking, Gemini-generated titles.

**Out of scope:** highlight-reel pipeline, captions/text overlays, automated rating pass beyond Gemini's own selection.

### Phase 5 — Test Backfill for v1 ✅

Tests for the four v1 modules with highest "silent wrong number" risk: `costs/`, `ingest/format_probe.py`, `indexer/` idempotency, `moments/` constraint enforcement. External APIs mocked; FFmpeg against tiny fixture videos; injectable clock; UTC-only.

### Phase 6 — VideoIndex as Long-Lived Asset ✅

`VideoIndex` model: `(id, source_video_id FK, provider, index_id, status, expires_at nullable, created_at)`. One row per source video per provider. Idempotency check queries `VideoIndex` directly. Inline expiry check before every query; Discord warning at T-7 days; background sweep in polling loop. Alembic migration backfills from existing `MomentDetectionRun` rows.

### Phase 7 — `Short` DB Model + Per-Short Cost Attribution ✅

`Short` model tracking per-clip pipeline state (pending → cutting → cut → pending_approval → approved → uploading → uploaded, plus failed/rejected/skipped). `clip_selection_id` FK added in Phase 8 when `ClipSelection` table is created. `gemini_rationale` and `gemini_rank` columns added in Phase 8 migration. `CostEvent.short_id` FK wired. Startup recovery resets cutting/uploading Shorts to pending.

### Phase 8 — Pegasus Describe + Gemini Clip Selection ✅

Two stages per video:

**Stage 1 — Pegasus describes the whole video in 10-second windows (one call).** Results written as `SegmentDescription` rows.

**Stage 2 — Gemini reads descriptions and selects clips.** Returns `{start_seconds, end_seconds, rationale, rank, source_segment_ids, title}`. Each selection becomes a `ClipSelection` row and a `Short` row.

**DB models:** `SegmentDescription`, `ClipSelection`, `GeminiRun`.

### Phase 9 — FFmpeg Cut + Reformat ✅

Three recipes: **blur-bg** (gameplay centered, blurred bars), **split** (facecam top strip, gameplay bottom), **passthrough** (already-vertical source). Re-encode to 1080×1920 H.264/AAC.

### Phase 10 — Discord Approval Gate ✅

`ApprovalRequest` model. Drive preview upload + Discord emoji reaction (👍/👎) flow. 48h timeout → auto-rejected.

### Phase 11 — YouTube Shorts Upload ✅

`videos.insert` with resumable upload. Quota tracking in `QuotaUsage` table. Overflow stays `approved` and picks up next UTC day.

---

## v2 Lessons

- **Gemini + text only → over-conservative selection.** When Gemini received only the text window descriptions, it produced fewer, lower-quality clips. Adding the source video restored quality. Prompt must frame the video as a "visual aid to confirm timestamps" not a second quality gate — otherwise Gemini becomes more selective, not more accurate.
- **15-second window boundaries as timestamps → wrong cut points.** Clips were starting and ending mid-action because Gemini snapped to the segment grid. Free timestamp selection fixed this; prompting for natural endpoints (all enemies dead, ace announcement, round-win, spike detonation) produces clean endings.
- **Constraint name in Alembic migration must match explicit name from prior migration.** The Phase 8 migration tried to drop `"shorts_moment_id_key"` (Postgres auto-name) but the constraint was created with explicit name `"uq_shorts_moment_id"`. Always use the explicit name from the `CREATE` statement, not the Postgres default.
- **`CreateAnalyzeTaskResponse.task_id`, not `.id`.** TwelveLabs SDK returns `task_id` on the analyze task response; `.id` does not exist. The `# type: ignore` comment masked the issue at type-check time.

---

## v3 — Polish, Reliability, Bug Fixes

**No new features.** v3 is exclusively about making what already exists work properly. Three things drive the scope: (1) bugs surfaced by real-world use of v2, (2) operational visibility that's been missing, (3) edge cases the v2 pipeline doesn't handle gracefully (oversized videos, Drive preview lag).

If a phase here starts feeling like "and while we're at it, let's add X" — that X belongs in v4, not v3.

### Phase 13 — Per-Step Error Classification ✅

Typed exception hierarchy in `src/errors/`. Each provider gets its own exception class with `step`, `provider`, and `to_discord()`. Top-of-pipeline handler in `processor.py` splits `PipelineError` (typed, step/provider context surfaced to Discord) from bare `Exception` (unexpected bugs).

Hierarchy: `PipelineError → PegasusError → {PegasusIndexError, PegasusDescribeError, PegasusQuotaError, PegasusVideoError}`, `GeminiError → {GeminiProbeError, GeminiSelectionError, GeminiQuotaError}`, `FFmpegPipelineError → {FFmpegProbeError, FFmpegCutError}`, `DriveError → {DriveAuthError, DriveDownloadError, DriveUploadError}`, `DiscordError → {DiscordPostError, DiscordReactionError}`, `YouTubeError → {YouTubeUploadError, YouTubeAuthError, YouTubeQuotaError}`.

### Phase 14 — Facecam Crop Bug Fix

The visible v2 bug: facecam videos are not being reformatted with the webcam-top, gameplay-bottom split correctly. The facecam region is being selected but not zoomed — the whole source frame is being used as the "facecam crop" because of a scale mismatch between the Gemini prompt and the consuming code.

**Root cause:** `format_probe.py` asked Gemini to return `facecam_size_pct` as "percentage of total frame area (0.0–100.0)" but `_facecam_crop_rect` in `ffmpeg/cut.py` treated the value as a fraction (0.0–1.0). Gemini returned e.g. 15.0; code computed `face_w = src_w * 15.0` → clamped to `src_w` = entire frame. So the "facecam crop" was the whole frame, and scaling it to the face slot just shrunk the entire source into that strip.

**Fix (Phase 14, shipped):**
1. `format_probe.py`: Changed `_FRAME_ANALYSIS_PROMPT` to ask for `facecam_size_pct` as a decimal fraction of frame WIDTH (0.0–1.0), e.g. 0.25 for a facecam spanning a quarter of the frame's width.
2. `ffmpeg/cut.py`: Fixed `_facecam_crop_rect` to handle "center" facecam position (previously mapped to bottom-right), added sensible clamp bounds (20px minimum, half-frame maximum), and updated docstring to match the corrected input contract.
3. `CLAUDE.md` Section 9: Lesson recorded — always make Gemini prompt unit and code docstring agree on scale (fraction vs percentage).

**Note:** Existing `FormatProfile` rows in the DB have `facecam_size_pct` in the old 0–100 scale. Re-run format probe on any previously processed facecam videos for the fix to take effect (the probe is idempotent only if a row already exists — delete the row to force a re-probe).

**Done when:** a facecam video re-processed after this fix shows the streamer's face zoomed to fill the top strip; the gameplay fills the bottom strip; the fixture test catches a broken filter graph.

**Third sub-task — fixture test (pending):** Add a 5-second fixture video to `tests/fixtures/` with a clearly identifiable colored square in the corner playing the role of "facecam". The golden-file test asserts the output has the colored square scaled and on top, not in its original position.

**Fourth sub-task — sanity-check the recipe against `facecam_size_pct`.** The recipe should work whether the facecam is 15% or 25% or 35% of the source frame width. Test with at least two real videos with different facecam sizes.

### Phase 15 — Oversized Video Pre-Processing

Pegasus rejects videos over 2 GB or longer than 2 hours. Currently those rejections surface as opaque API errors. Pre-process at ingest so the operator never hits the Pegasus 2GB/2hr wall.

**Detection:** during the existing FFprobe step, check `duration_seconds` and `file_size_bytes`. If either exceeds the configured limit (default: 1.9GB, 1h 55m — slightly under Pegasus's hard cap to leave margin), branch into pre-processing before indexing.

**Pre-processing:**
- **Oversized by file size only (under 2hr but over 2GB)** — re-encode with a lower bitrate. CRF 24 (more aggressive than the cutting/reformat default of 23 because this is the intermediate file Pegasus sees, not the final short).
- **Oversized by duration (over 2hr)** — refuse with a typed `SourceTooLongError`. Splitting changes narrative continuity. Better to ask the operator to manually trim.
- **Oversized by both** — same as "duration only": refuse with `SourceTooLongError`.

Pre-processed file replaces original for downstream steps only. Original in Drive untouched. Cleaned up on every code path including errors.

**Done when:** a 3GB / 1h video successfully runs through the pipeline (pre-processed first); a 3h video is rejected cleanly with a Discord ping; an under-limit video skips pre-processing entirely.

### Phase 16 — End-to-End Timing Instrumentation

Every step gets timed. Durations land in the DB so they can be trended over time, and in the Discord summary so the operator sees them per-run.

**New DB model: `StepTiming`** — `(id, source_video_id FK, short_id FK nullable, step_name, started_at, ended_at, duration_seconds, succeeded BOOLEAN, created_at)`. One row per pipeline step per run, including failures.

**Steps to instrument:** `drive_download`, `ffprobe`, `frame_extraction`, `gemini_format_probe`, `oversized_preprocess`, `pegasus_index`, `pegasus_describe`, `gemini_clip_selection`, `ffmpeg_cut` (per-short), `ffmpeg_reformat` (per-short), `drive_upload_preview` (per-short), `discord_post_approval`, `approval_wait`, `youtube_upload` (per-short).

**Discord summary** gains a timing block.

**Done when:** every step writes a `StepTiming` row on completion; Discord summary includes the timing block; `SELECT step_name, AVG(duration_seconds) FROM step_timing GROUP BY step_name` produces meaningful averages.

### Phase 17 — Sync Pipeline Completion Before Discord Approval

Currently clips are announced to Discord individually as they're cut. Two problems: Drive previews aren't transcoded yet, and the operator gets dripped clips one at a time.

**Change A** — Wait for all clips to finish cutting+reformatting before any Discord posting.

**Change B** — After uploading each clip to Drive, poll `files.get` for `videoMediaMetadata` (available only after transcoding finishes). Poll every 10 seconds, 5-minute hard timeout. Timeout → post with "⏳ Preview may still be processing" note rather than blocking.

**Change C** — `videoMediaMetadata` polling is its own `StepTiming` step.

**Done when:** zero Discord messages until all clips are cut+reformatted+Drive-transcoded; clicking the Drive preview link immediately renders the video; timed-out clips get the warning note.

### Phase 18 — Summary-First Discord Layout in Per-Video Threads

**Change A** — Each source video gets its own Discord thread.

**Change B** — First message in thread is a summary placeholder ("Processing…"), edited in place once clips are ready with real numbers (facecam, format, clip count, cost so far). Clip messages appear below, sorted by `gemini_rank` desc. Summary edited again at completion with final upload count.

**Done when:** each source gets its own thread; summary message goes through "processing" → "ready for approval" → "complete" lifecycle; clip messages appear below sorted by rank.

### Phase 19 — Bug Sweep + v2 Backlog

A catch-all phase for small bugs and inconsistencies surfaced while running v2 in production. Every bug fixed here gets a regression test, and every fix has a one-line entry in `CLAUDE.md` Section 9.

**Done when:** no outstanding bug reports from the operator after a week of v2 operation; every fix has a test; `CLAUDE.md` Section 9 has new lessons.

---

## Out of scope for v3

- Captions / text overlays
- Cyclic / sliding-window clip selection
- New category prompts (tutorial/podcast)
- Prompt replay tooling
- Multi-channel
- Web UI
- Highlight reel export
- Canary unlisted-first-of-day

---

## v3 Risks

- **Diagnostic-before-fix discipline on the facecam bug.** Check the DB row for `has_facecam` before editing FFmpeg code. The Phase 14 fix addresses the confirmed diagnosis (split recipe running but crop rect wrong). If a video shows the blur-bg recipe instead, the probe is the bug, not the filter graph.
- **The `StepTiming` table grows unboundedly.** Each source produces 10–15 rows. Worth a retention policy (drop rows older than 90 days?) but not urgent enough to gate v3 on.
- **Drive `videoMediaMetadata` polling adds latency to every clip.** If most clips transcode in 5 seconds and the timeout is 5 minutes, the average case is fine but the worst case is 5 minutes per clip. Measure with Phase 16, tune the timeout down if real numbers support it.
- **Thread permissions on Discord.** Adding bot permissions in prod can fail silently if the bot is already in the channel under restricted scope. Test in a dev channel first.
- **Pre-processing CRF 24 might be too aggressive for Pegasus.** If Pegasus's describe quality drops on pre-processed videos, raise the CRF to 22. Test by comparing descriptions from original vs. pre-processed.
- **Phase 17 is a behavior change.** Waiting for all clips before any Discord post means the operator doesn't know anything is happening for several minutes. The placeholder summary in Phase 18 mitigates this — don't ship 17 without 18.

---

## v4 — Quality, Reach & Operational Control (deferred)

> Paused until v3 ships and runs in prod.

**Goal:** Given a working end-to-end pipeline, make the output better, add new content types, and give the operator more control without touching the core pipeline.

**In scope:** canary upload mode, prompt replay tooling, caption burn-in, tutorial/podcast category prompts, highlight reel pipeline from existing `SegmentDescription` rows, cyclic/sliding-window clip selection.

**Out of scope:** multi-account YouTube upload, automated thumbnail generation, A/B testing different Gemini prompts.

### Phase A — Canary Upload Mode

First Short uploaded each UTC day goes up as `unlisted`; operator gets a Discord ping with the YouTube URL. Subsequent Shorts that day go `public` automatically.

`CANARY_UNLISTED_FIRST_OF_DAY` env var (default `true`).

### Phase B — Prompt Replay Tooling

Re-run Gemini clip selection against existing `SegmentDescription` rows without re-calling Pegasus.

`scripts/replay_gemini.py` — reads `SegmentDescription` rows from DB, re-downloads source video from Drive (still in `processed/`), calls `select_clips`, writes new `ClipSelection` + `Short` rows tagged with a `prompt_version` suffix.

### Phase C — Caption Burn-in (Optional, Per-Category)

Captions drawn by Gemini per clip, burned in via FFmpeg `drawtext`. No SRT sidecar.

`CAPTIONS_ENABLED_GAMEPLAY=true` env var gates per category. Gemini generates `[{text, start_seconds, end_seconds, position}]`. Pydantic validates. `ffmpeg/captions.py` builds `drawtext` filter chain. New `CostEvent` step: `captions.gemini_spec`.

### Phase D — Tutorial and Podcast Category Prompts

Write `prompts/pegasus/tutorial.txt`, `prompts/pegasus/podcast.txt`, `prompts/gemini/tutorial.txt`, `prompts/gemini/podcast.txt`. Only the selection criteria change; same pipeline.

### Phase E — Highlight Reel Export

`scripts/export_highlight_reel.py` — reads `SegmentDescription` rows, calls Gemini with a highlight-reel editor prompt, writes `[{start_seconds, end_seconds, label, notes}]` JSON locally and optionally to Drive. Read-only export, no new Short rows, no upload.

### Phase F — Cyclic / Sliding-Window Clip Selection

Overlap the Pegasus describe windows by a few seconds to avoid missing action that straddles a boundary. Update Gemini's clip-selection prompt to deduplicate near-identical windows when merging.
