from decimal import Decimal

# Bump this string whenever any price changes — never silently edit an existing entry.
PRICING_VERSION = "2026-05-v1"

# Prices last verified: 2026-05-20.
# Sources:
#   Gemini 2.5 Flash: https://ai.google.dev/gemini-api/docs/pricing
#   TwelveLabs Pegasus: https://docs.twelvelabs.io/docs/pricing  ← VERIFY before first billing run
#   YouTube Data API: https://developers.google.com/youtube/v3/determine_quota_cost
PRICES: dict[str, Decimal] = {
    # Gemini 2.5 Flash — cost per individual token
    "gemini_flash_input_per_token": Decimal("0.075") / Decimal("1000000"),   # $0.075 / 1M tokens
    "gemini_flash_output_per_token": Decimal("0.30") / Decimal("1000000"),   # $0.30  / 1M tokens

    # TwelveLabs Pegasus 1.2 — per video-minute indexed.
    # WARNING: This is an estimate. Confirm the exact figure at the URL above before production.
    "pegasus_index_per_minute": Decimal("0.05"),

    # TwelveLabs search query — per call.
    # WARNING: May be bundled with the indexing tier. Verify before treating as additive cost.
    "pegasus_query_per_call": Decimal("0.002"),

    # YouTube Data API — quota units consumed per Shorts upload (not USD; tracked separately).
    "youtube_upload_quota_units": Decimal("1600"),
}
