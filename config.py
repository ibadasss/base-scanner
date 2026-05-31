"""
Configuration for the Base Utility Screener.

All tunable thresholds and API settings live here so you can adjust the
screener's behaviour without touching the core logic.

The whole pipeline is designed to run KEYLESS using free public APIs:
  - DefiLlama        : protocol TVL / fees / revenue
  - GeckoTerminal    : DEX pools (token discovery)
  - honeypot.is      : honeypot / tax detection
  - Sourcify         : contract verification (keyless!)
  - Base public RPC  : on-chain balanceOf for smart-money tracking
  - Warpcast         : Farcaster social buzz (keyless public search)

Optional API keys only *enhance* things; nothing core depends on them.
"""
from __future__ import annotations

import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # python-dotenv not installed -> just rely on real env vars
    pass


# --- Network ---------------------------------------------------------------
CHAIN_SLUG = "base"          # DefiLlama / GeckoTerminal chain slug
CHAIN_ID = 8453              # Base mainnet chain id (honeypot.is / Sourcify / RPC)
CHAIN_NAME = "Base"          # As it appears in DefiLlama "chains" lists

# Official Base public RPC. Keyless and reliable; supports batch eth_call.
# Swap for a paid RPC (Alchemy/QuickNode) if you hit rate limits.
BASE_RPC_URL = os.getenv("BASE_RPC_URL", "https://mainnet.base.org").strip()

# Multiple public RPCs for fallback/rotation -- heavy getLogs + batch bursts
# can rate-limit a single endpoint, so we spread load across several.
BASE_RPC_URLS = [u for u in [
    BASE_RPC_URL,
    "https://base.publicnode.com",
    "https://base.drpc.org",
    "https://base.meowrpc.com",
] if u]
# de-duplicate while preserving order
BASE_RPC_URLS = list(dict.fromkeys(BASE_RPC_URLS))

# --- HTTP ------------------------------------------------------------------
REQUEST_TIMEOUT = 20         # seconds per HTTP request
REQUEST_RETRIES = 3          # retry attempts on transient failures
USER_AGENT = "base-utility-screener/0.2 (research bot)"

# --- API keys (all optional) -----------------------------------------------
# Etherscan V2 key: NOTE Base (8453) is NOT on Etherscan's free tier, so we use
# Sourcify for verification by default. Kept here for users on a paid plan.
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "").strip()
# Neynar key: optional, gives richer Farcaster data. Falls back to Warpcast.
NEYNAR_API_KEY = os.getenv("NEYNAR_API_KEY", "").strip()
# Telegram alerts: set both to receive Smart Money Monitor notifications.
#   TELEGRAM_BOT_TOKEN -> from @BotFather
#   TELEGRAM_CHAT_ID   -> your user/group/channel id (see README)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# --- Endpoints -------------------------------------------------------------
DEFILLAMA_PROTOCOLS = "https://api.llama.fi/protocols"
DEFILLAMA_FEES = "https://api.llama.fi/overview/fees/{chain}"
GECKOTERMINAL_BASE = "https://api.geckoterminal.com/api/v2"
HONEYPOT_API = "https://api.honeypot.is/v2/IsHoneypot"
ETHERSCAN_V2 = "https://api.etherscan.io/v2/api"
SOURCIFY_V2 = "https://sourcify.dev/server/v2/contract/{chain_id}/{address}"
WARPCAST_SEARCH = "https://api.warpcast.com/v2/search-casts"
NEYNAR_SEARCH = "https://api.neynar.com/v2/farcaster/cast/search"
TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

# --- Screening thresholds (Safety + data quality) --------------------------
# Protocols below this Base TVL are too small / illiquid to trust for an MVP.
MIN_TVL_USD = 250_000
# Drop protocols whose TVL is bleeding out hard (likely dying / exploited).
MAX_TVL_DROP_7D_PCT = -50.0
# Require at least some real fee generation over 30d to count as "utility".
MIN_FEES_30D_USD = 1_000

# --- Utility scoring weights (fundamental pillar; sum = 100) ----------------
# The rubric we agreed on. NOTE: 'value' now prefers REVENUE/mcap (a real
# earnings yield) over raw fees, falling back to fees when revenue is missing.
UTILITY_WEIGHTS = {
    "value": 30,        # annualised revenue / market cap  (earnings yield)
    "stickiness": 20,   # TVL trend stability (not bleeding out)
    "activity": 30,     # recent fee run-rate vs trend     (real, live usage)
    "scale": 20,        # absolute TVL footprint           (established enough)
}

# --- Composite scoring weights (blends the three pillars) -------------------
# Final ranking = weighted blend of:
#   utility      : fundamentals (always available)
#   smart_money  : are tracked profitable wallets holding it? (needs seed list)
#   social       : Farcaster buzz momentum                    (keyless)
# Weights are renormalised per-candidate over whichever pillars have data,
# so a token without smart-money/social data is never unfairly penalised.
COMPOSITE_WEIGHTS = {
    "utility": 60,
    "smart_money": 25,
    "social": 15,
}

# --- Smart money tracking --------------------------------------------------
# >>> THIS LIST IS YOUR EDGE. <<<
# Curate addresses of wallets with a proven, profitable track record on Base
# (funds, early LPs, consistent winners). The screener checks how many of
# them currently hold each candidate token. Quality of this list directly
# determines the quality of the smart-money signal.
#
# Leave empty to disable the smart-money pillar (composite will use the other
# pillars). Populate over time as you identify good wallets.
SMART_MONEY_WALLETS: list[str] = [
    # --- Discovered via `python screener.py --discover` (accumulated >=2 winner
    # tokens AND still holding them; nonce-filtered, contract-filtered). Review
    # on DeBank before fully trusting -- these are momentum-conviction wallets. ---
    "0x5aafc1f252d544f744d17a4e734afd6efc47ede4",  # 3 winners held, nonce 37976
    "0x73d8bd54f7cf5fab43fe4ef40a62d390644946db",  # 2 winners held
    "0x2d5cd0905f246688b75f57f2e88d2b18e67407cc",  # 2 winners held
    "0x4446adc0b8136ffc55ddb7a488ba5509ace2a5ef",  # 2 winners held
    "0xf6414154ea3495ee098a88c8e35e47769d6fb205",  # 2 winners held
    "0x8416aa80ce2036551f9db02d47a6c611d61520af",  # 2 winners held
    "0xb7c8f170c51dfb86d14b2e1466db5ba29a3d1cea",  # 2 winners held
    "0x035ae7d933dcbfe617ffba194a88af0c2867b90c",  # 2 winners held
    "0x8782163068c7cd74d2510768a61135c1e4eb07b3",  # 2 winners held
    "0x96973f7b83a3c785d94e0a6d8712174abb81b748",  # 2 winners held
    "0x4f82e73edb06d29ff62c91ec8f5ff06571bdeb29",  # 2 winners held
    "0xc14c253b38c331f4c5d969e59a9a43d5fa4ee8d7",  # 2 winners held
]
# A wallet counts as "holding" if its raw token balance exceeds this (wei-like)
# floor, which filters out airdrop dust.
SMART_MONEY_DUST_FLOOR = 1
# Max wallets to expect holding for full score normalisation. If you track
# many wallets, a token held by ~this many is treated as a maximal signal.
SMART_MONEY_SATURATION = 5

# --- Smart-money DISCOVERY -------------------------------------------------
# Settings for the discovery tool (`python screener.py --discover`), which
# finds candidate smart-money wallets by spotting wallets that make large buys
# across MULTIPLE strong-performing Base tokens (recurring winners = skill).
DISCOVERY = {
    "pool_pages": 3,                 # pages of top Base pools to scan (~20/page)
    "min_pool_liquidity_usd": 120_000,
    "min_volume_24h_usd": 120_000,   # ignore thin pools with fake-ish action
    "min_price_change_h24": 8.0,     # % 24h gain to count a token as a "winner"
    "max_price_change_h24": 400.0,   # exclude hyper-pump / likely-rug memes
    "max_winners": 10,               # cap winner pools scanned (rate-limit safety)
    "min_buy_usd": 1_000,            # only count buys at least this large
    "min_distinct_winners": 2,       # wallet must buy >= this many distinct winners
    "infra_min_winners": 6,          # accumulating >= this many winners = CEX/router/infra
    "include_smart_wallets": True,   # keep smart-contract wallets (common on Base), tagged
    # Accumulation engine (eth_getLogs on Transfer events):
    "accum_window_blocks": 8000,     # how far back to measure net accumulation (~4-5h)
    "accum_chunk_blocks": 2000,      # getLogs sub-range size (avoid huge responses)
    "accum_top_per_token": 50,       # top net accumulators per token to keep
    "max_nonce": 100000,             # exclude only extreme automation (CEX/MM: 100k-millions of txs)
    "min_distinct_tokens": 2,        # must accumulate >= this many winners (skill, not luck)
    "drop_big_contracts": True,      # drop large-bytecode contracts (routers/fillers)
    "verify_holdings": True,         # require candidate still holds >=1 winner now
    "top_n": 25,                     # candidates to print
    "out_file": "smart_money_candidates.txt",
}

# --- Social (Farcaster) signal ---------------------------------------------
SOCIAL_LOOKBACK_DAYS = 14    # only count casts from the last N days
SOCIAL_MAX_CASTS = 100       # cap casts fetched per protocol (rate friendliness)

# --- Enrichment ------------------------------------------------------------
# Smart-money + social checks are per-token and hit rate-limited APIs, so we
# only enrich the top-N fundamentally-ranked candidates by default.
ENRICH_TOP_N = 15

# --- Output ----------------------------------------------------------------
DEFAULT_TOP_N = 25
DEFAULT_CSV_PATH = "base_utility_ranking.csv"

# Backwards-compat alias (older code referenced WEIGHTS).
WEIGHTS = UTILITY_WEIGHTS



# --- Smart Money Monitor + Consensus Alert ---------------------------------
# `python screener.py --monitor` scans recent on-chain Transfers INTO your
# SMART_MONEY_WALLETS, detects tokens freshly accumulated by several of them
# (consensus), filters spam/scams, and alerts (console + optional Telegram).
GECKOTERMINAL_TOKEN = "https://api.geckoterminal.com/api/v2/networks/{chain}/tokens/{address}"

# Common base/quote tokens to ignore -- receiving these is not a "buy signal".
MONITOR_EXCLUDE_TOKENS = {
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC
    "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca",  # USDbC
    "0x4200000000000000000000000000000000000006",  # WETH
    "0x50c5725949a6f0c72e6c4a641f24049a917db0cb",  # DAI
    "0x2ae3f1ec7f1f5012cfeab0185bfc7aa3cf0dec22",  # cbETH
    "0xfde4c96c8593536e31f229ea8f37b2ada2699bb2",  # USDT
    "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf",  # cbBTC
    "0x60a3e35cc302bfa44cb288bc5a4f316fdb1adb42",  # EURC
    "0xc1cba3fcea344f92d9239c08c0568f6f2f0ee452",  # wstETH
}

MONITOR = {
    "lookback_blocks": 1800,       # how far back to scan per run (~1h on Base)
    "chunk_blocks": 900,           # getLogs sub-range size
    "min_consensus": 2,            # >= this many smart wallets buying same token -> alert
    "alert_cooldown_sec": 43_200,  # don't re-alert the same token within 12h
    "min_token_liquidity_usd": 50_000,  # token must have real liquidity (filters spam airdrops)
    "min_token_volume_24h_usd": 20_000,
    "check_honeypot": True,        # drop honeypots before alerting
    "max_validate": 30,            # cap consensus tokens validated per run (API budget)
    "state_file": "monitor_state.json",
}
