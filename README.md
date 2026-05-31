# Base Utility Screener 🔎

A research bot that finds **fundamentally strong projects on the Base network** and filters out the noise (scams, dead protocols, pure-hype tokens).

It does **not** trade. It is a *fundamental + signal screener* built on the idea that, for retail, the biggest edge is **discipline + data**: avoid rug pulls first, then surface protocols with real utility (revenue, sticky liquidity, real usage), and confirm with **smart-money conviction** and **Farcaster buzz**.

> ⚠️ **Not financial advice.** Strong fundamentals do not guarantee price appreciation. This is a research tool to tilt probability in your favour and to keep you away from obvious traps. Always DYOR.

It is **fully keyless** — every layer runs on free public APIs. Optional API keys only enhance results.

---

## How it works

The pipeline is **layered**, so the cheapest/most-important checks run first and expensive per-token signals only run on the best candidates:

```
1. FETCH    Pull Base protocols + fees/revenue from DefiLlama
2. SAFETY   Drop scams / dead / illiquid protocols (data-quality filter)
3. SCORE    Rank survivors 0-100 on a fundamental utility rubric
4. ENRICH   For the top-N, add Smart-Money + Farcaster signals -> composite score
5. REPORT   Print top candidates + export full ranking to CSV
```

### Layer 3 — the utility score (fundamentals, 0-100)

| Component   | Weight | What it measures                                          |
|-------------|:------:|-----------------------------------------------------------|
| `value`     |   30   | Annualised **revenue** ÷ market cap — *real earnings yield* |
| `stickiness`|   20   | TVL trend not bleeding out — *sticky liquidity*           |
| `activity`  |   30   | Recent fee run-rate vs. trend — *live, real usage*        |
| `scale`     |   20   | Absolute TVL footprint — *established enough*             |

### Layer 4 — the composite score (Phase 2)

The final ranking blends three pillars:

| Pillar        | Weight | Source                                                       |
|---------------|:------:|--------------------------------------------------------------|
| `utility`     |   60   | The fundamental score above (always available)               |
| `smart_money` |   25   | How many **curated proven wallets** hold the token (on-chain) |
| `social`      |   15   | **Farcaster buzz** momentum (Base-native early signal)       |

Weights are **renormalised per-candidate** over whichever pillars have data, so a token without smart-money/social data is never unfairly penalised.

> **The retail sweet spot:** high composite + low market cap + low mainstream attention = strong fundamentals the market hasn't fully repriced yet.

All weights and thresholds are editable in [`config.py`](./config.py).

---

## Smart-money tracking — *this is your edge*

The smart-money pillar checks how many wallets from a **curated seed list** currently hold each candidate token, read directly from the Base chain via batched `balanceOf` calls (keyless, official Base RPC).

The signal is only as good as your list. Populate it in [`config.py`](./config.py):

```python
SMART_MONEY_WALLETS = [
    "0xWalletOfAProvenBaseWinner",
    "0xEarlyLPThatIsConsistentlyRight",
    # ... curate over time
]
```

If the list is empty, the smart-money pillar is automatically **disabled** and the composite uses the remaining pillars.

### Don't have a seed list? Discover one — `--discover`

The discovery tool builds a candidate seed list **keylessly** from on-chain data. Because a swap's `tx_from` on Base is usually a router/aggregator/ERC-4337 bundler (not the user), it ignores swaps and looks at the ground truth — **ERC-20 Transfer events**:

```
1. Find "winner" tokens (trending + top pools: liquid, rising, not a rug-pump)
2. Measure NET ACCUMULATION per address via eth_getLogs over a recent window
3. Drop CEX/MM/bots (by nonce) and routers/pools (by bytecode + net flow)
4. Keep wallets that STILL HOLD what they accumulated (real conviction)
5. Rank wallets that accumulated MULTIPLE winners (skill, not luck)
```

```bash
python3 screener.py --discover     # prints candidates + writes smart_money_candidates.txt
```

Review the candidates (the file includes a DeBank-ready list), paste the good ones into `SMART_MONEY_WALLETS`, and re-run the screener. It's a rolling snapshot — run it periodically to grow your list.

---

## Setup

Requires Python 3.10+.

```bash
pip install -r requirements.txt
```

All core features work with **no API keys**. Optional keys (copy `.env.example` to `.env`):

- `ETHERSCAN_API_KEY` — only useful on a paid plan (Base is **not** on Etherscan's free tier; we use Sourcify for verification by default).
- `NEYNAR_API_KEY` — richer Farcaster data (otherwise the keyless Warpcast search is used).
- `BASE_RPC_URL` — override the default public RPC if you hit rate limits.

---

## Usage

```bash
# Top 25 by composite score (enriched) -> console + CSV
python3 screener.py

# Fundamentals only, fast (skip smart-money + social)
python3 screener.py --no-enrich

# Enrich the top 20 candidates instead of the default
python3 screener.py --enrich-top 20

# Show top 10
python3 screener.py --top 10

# See WHY protocols were rejected by the safety filter
python3 screener.py --show-rejected

# Token-level safety check on a single address (honeypot + Sourcify verification)
python3 screener.py --check 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913

# Discover candidate smart-money wallets from on-chain accumulation
python3 screener.py --discover
```

### Example output

```
 #  PROTOCOL              CAT            COMP  UTIL    SM   SOC       TVL   FEES30D
-----------------------------------------------------------------------------------
 1  Uniswap V4            Dexs           69.1  61.4     - 100.0    $53.9M    $12.0M
 2  Uniswap V3            Dexs           67.8  62.0     -  91.3   $312.1M     $7.2M
 3  Aave V3               Lending        63.6  58.4     -  84.5   $410.7M   $993.0K
 4  Limitless Exchange    Prediction M   63.1  78.9     -   0.0   $764.7K     $3.9M
 ...

  COMP=composite  UTIL=utility(fundamentals)  SM=smart-money  SOC=Farcaster social  ('-' = no data)
```

The full ranking (all survivors, with pillar breakdowns) is written to `base_utility_ranking.csv`.

---

## Project structure

```
utility-screener/
├── config.py        # thresholds, scoring weights, endpoints, smart-money seed list
├── fetch.py         # DefiLlama (TVL/fees) + GeckoTerminal (new pools) fetchers
├── safety.py        # data-quality filter + honeypot.is + Sourcify verification
├── score.py         # utility rubric + composite scoring (pillar blend)
├── smartmoney.py    # keyless on-chain smart-money tracker + RPC helpers (multi-endpoint)
├── social.py        # Farcaster buzz via keyless Warpcast search
├── discover.py      # keyless smart-money discovery (accumulation via eth_getLogs)
├── screener.py      # main pipeline + CLI
├── requirements.txt
├── .env.example
└── README.md
```

---

## Tuning the rubric

Open [`config.py`](./config.py):

- `UTILITY_WEIGHTS` — rebalance the four fundamental components
- `COMPOSITE_WEIGHTS` — how much to trust fundamentals vs. smart money vs. social
- `MIN_TVL_USD`, `MAX_TVL_DROP_7D_PCT`, `MIN_FEES_30D_USD` — safety thresholds
- `SMART_MONEY_WALLETS`, `SMART_MONEY_SATURATION`, `SMART_MONEY_DUST_FLOOR` — smart-money behaviour
- `SOCIAL_LOOKBACK_DAYS`, `SOCIAL_MAX_CASTS` — social window
- `ENRICH_TOP_N` — how many candidates get the (slower) per-token enrichment

---

## Roadmap

Phase 1 (Safety + Utility) and Phase 2 (Smart Money + Social + composite tuning) are **done**. Next:

- [x] **Layer 1 — Safety:** scam/dead/illiquid filter + honeypot + verification
- [x] **Layer 2 — Utility:** revenue/usage/stickiness fundamental score
- [x] **Layer 3 — Smart Money:** curated-wallet on-chain holdings signal
- [x] **Layer 4 — Social:** Farcaster buzz momentum
- [x] **Smart-money discovery:** keyless wallet discovery via on-chain accumulation (`--discover`)
- [ ] **Backtesting:** validate the rubric would have caught known winners early
- [ ] **Alerts:** push top movers to Telegram/Discord
- [ ] **New-pool scanning:** auto early-detection loop over `fetch.fetch_new_pools()` + `safety.assess_token()`

---

## Data sources

- [DefiLlama](https://defillama.com/docs/api) — TVL, fees, revenue (free, keyless)
- [GeckoTerminal](https://www.geckoterminal.com/dex-api) — DEX pools, prices, volume (free, keyless)
- [honeypot.is](https://honeypot.is/) — honeypot / tax detection (free, keyless)
- [Sourcify](https://sourcify.dev/) — contract verification (free, keyless)
- [Base public RPC](https://docs.base.org/) — on-chain balances, nonces, code & Transfer logs (keyless; rotates across mainnet.base.org, publicnode, drpc, meowrpc for resilience)
- [Warpcast / Farcaster](https://warpcast.com/) — social buzz (free, keyless); [Neynar](https://neynar.com/) optional
