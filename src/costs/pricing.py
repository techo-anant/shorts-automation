from decimal import Decimal

# Bump this string whenever any price changes — never silently edit an existing entry.
PRICING_VERSION = "2026-05-v3"

# Prices last verified: 2026-05-21.
# Sources:
#   Gemini 2.5 Flash:  https://ai.google.dev/gemini-api/docs/pricing
#   TwelveLabs Pegasus 1.5: https://www.twelvelabs.io/pricing
#   YouTube Data API:  https://developers.google.com/youtube/v3/determine_quota_cost
PRICES: dict[str, Decimal] = {
    # --- Gemini 2.5 Flash ---
    "gemini_flash_input_per_token": Decimal("0.075") / Decimal("1000000"),   # $0.075 / 1M tokens
    "gemini_flash_output_per_token": Decimal("0.30") / Decimal("1000000"),   # $0.30  / 1M tokens

    # --- TwelveLabs Pegasus 1.5 ---
    # Charged per video-minute for the analyze call
    "pegasus_input_per_minute": Decimal("0.0292"),
    # Output tokens from the analyze response
    "pegasus_output_per_1k_tokens": Decimal("0.0075"),

    # --- TwelveLabs Marengo 3.0 (legacy v1 — kept for historical CostEvent records) ---
    "marengo_index_per_minute": Decimal("0.042"),
    "marengo_search_per_query": Decimal("0.004"),

    # --- YouTube Data API ---
    # Quota units per Shorts upload (not USD; tracked separately via QuotaUsage)
    "youtube_upload_quota_units": Decimal("1600"),
}
