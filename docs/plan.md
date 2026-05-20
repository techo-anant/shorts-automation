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

## Deviations from Original Plan

| Original plan | What actually shipped | Reason |
|---|---|---|
| TwelveLabs **Pegasus** as primary model | **Marengo 3.0** used instead | Marengo is ~2× cheaper ($0.042/min index + $0.004/query vs Pegasus ~$0.029/min/call). Marengo can be queried multiple times against one index, making it better for the dual use-case (Shorts + highlight reels from one upload). Pegasus stub kept for future swap if bake-off warrants it. |
| Single query per video | Two queries (`shorts` + `highlight_reel`) run concurrently | Enables both YouTube Shorts output and long-form highlight timestamps from one index; negligible extra cost ($0.008 per video for both queries). |

---
