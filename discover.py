"""
Smart-money DISCOVERY for the Base Utility Screener (Phase 2+).

Problem: you want a smart-money seed list but don't have one yet.

Why naive approaches fail on Base: a DEX trade's `tx_from_address` is usually a
router / aggregator / ERC-4337 bundler, and even the in-transaction token
recipient is often an intent-filler/settlement contract -- not the end user.

So we ignore swaps and look at the GROUND TRUTH instead: ERC-20 Transfer events.
For each strong-performing ("winner") token we measure NET ACCUMULATION per
address over a recent window via `eth_getLogs`. A wallet that:
  - net-accumulates SEVERAL winner tokens (skill, not luck),
  - is NOT a CEX/MM/bot (filtered by nonce),
  - is NOT a router/pool/big contract (filtered by bytecode + net flow),
  - and STILL HOLDS what it accumulated (conviction),
is a strong smart-money candidate.

Everything is KEYLESS (GeckoTerminal + Base public RPC).

IMPORTANT: these are *candidates*, not vetted smart money. It's a rolling
snapshot -- run periodically and always sanity-check a wallet (DeBank/Basescan)
before adding it to config.SMART_MONEY_WALLETS.
"""
from __future__ import annotations

import time
from collections import Counter, defaultdict
from typing import Any, Dict, List, Tuple

import requests

import config
import smartmoney

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO = "0x0000000000000000000000000000000000000000"
DEAD = "0x000000000000000000000000000000000000dead"


def _get(url: str, params: Dict[str, Any] | None = None) -> Any | None:
    """GET with strong 429 backoff (GeckoTerminal free tier is ~30 req/min)."""
    headers = {"User-Agent": config.USER_AGENT, "Accept": "application/json"}
    for attempt in range(1, 5):
        try:
            resp = requests.get(url, params=params, headers=headers,
                                timeout=config.REQUEST_TIMEOUT)
            if resp.status_code == 429:
                wait = 6 * attempt
                print(f"  [info] rate-limited, waiting {wait}s ...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            if attempt == 4:
                print(f"  [warn] GET failed {url}: {exc}")
                return None
            time.sleep(3 * attempt)
    return None


def _rpc(method: str, params: list) -> Any | None:
    """Single JSON-RPC call with retries + endpoint rotation (for getLogs)."""
    endpoints = getattr(config, "BASE_RPC_URLS", [config.BASE_RPC_URL])
    for url in endpoints:
        for attempt in range(1, 3):
            try:
                resp = requests.post(
                    url,
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                    headers={"User-Agent": config.USER_AGENT,
                             "Content-Type": "application/json"},
                    timeout=config.REQUEST_TIMEOUT + 15,
                )
                if resp.status_code == 429:
                    time.sleep(2 * attempt)
                    continue
                resp.raise_for_status()
                body = resp.json()
                if "result" in body:
                    return body["result"]
                time.sleep(1.0)
            except Exception:  # noqa: BLE001 - rotate to next endpoint
                time.sleep(1.0)
                break
    return None


def _strip_chain(token_id: str) -> str:
    return token_id.split("_", 1)[1] if "_" in token_id else token_id


def _topic_addr(topic: str) -> str:
    return "0x" + topic[-40:].lower()


# --------------------------------------------------------------------------
# Step 1: winner tokens
# --------------------------------------------------------------------------
def _passes(a: Dict[str, Any]) -> Tuple[bool, float]:
    cfg = config.DISCOVERY
    liq = float(a.get("reserve_in_usd") or 0)
    vol24 = float((a.get("volume_usd") or {}).get("h24") or 0)
    try:
        chg_h24 = float((a.get("price_change_percentage") or {}).get("h24"))
    except (TypeError, ValueError):
        chg_h24 = 0.0
    ok = (liq >= cfg["min_pool_liquidity_usd"]
          and vol24 >= cfg["min_volume_24h_usd"]
          and cfg["min_price_change_h24"] <= chg_h24 <= cfg["max_price_change_h24"])
    return ok, chg_h24


def fetch_winner_tokens() -> List[Dict[str, Any]]:
    """Strong-performing Base tokens (trending + top pools), de-duped by token."""
    cfg = config.DISCOVERY
    seen_pool: set[str] = set()
    seen_token: set[str] = set()
    winners: List[Dict[str, Any]] = []
    pool_addrs: set[str] = set()

    def _ingest(pools: List[Dict[str, Any]]) -> None:
        for p in pools or []:
            a = p.get("attributes", {}) or {}
            pool = (a.get("address") or "").lower()
            if pool:
                pool_addrs.add(pool)
            if not pool or pool in seen_pool:
                continue
            ok, chg_h24 = _passes(a)
            if not ok:
                continue
            seen_pool.add(pool)
            base = (p.get("relationships", {}).get("base_token", {}).get("data", {}) or {})
            token = _strip_chain(base.get("id", "")).lower()
            if not token or token in seen_token:
                continue
            seen_token.add(token)
            winners.append({"pool": pool, "name": a.get("name"),
                            "token": token, "change_h24": chg_h24})

    trending = _get(
        f"{config.GECKOTERMINAL_BASE}/networks/{config.CHAIN_SLUG}/trending_pools",
        {"duration": "24h"})
    if trending:
        _ingest(trending.get("data", []))
    time.sleep(3.0)
    for page in range(1, cfg["pool_pages"] + 1):
        data = _get(f"{config.GECKOTERMINAL_BASE}/networks/{config.CHAIN_SLUG}/pools",
                    {"page": page})
        if data:
            _ingest(data.get("data", []))
        time.sleep(3.0)

    winners.sort(key=lambda w: w["change_h24"], reverse=True)
    winners = winners[: cfg["max_winners"]]
    print(f"  [ok] winner tokens selected: {len(winners)}")
    return winners, pool_addrs


# --------------------------------------------------------------------------
# Step 2: net accumulation per token (via Transfer logs)
# --------------------------------------------------------------------------
def accumulate_token(token: str, latest: int) -> Dict[str, int]:
    """Net token inflow (received - sent) per address over the recent window."""
    cfg = config.DISCOVERY
    net: Dict[str, int] = defaultdict(int)
    window = cfg["accum_window_blocks"]
    chunk = cfg["accum_chunk_blocks"]
    start_block = latest - window
    b = start_block
    while b <= latest:
        to_b = min(b + chunk - 1, latest)
        logs = _rpc("eth_getLogs", [{
            "address": token,
            "topics": [TRANSFER_TOPIC],
            "fromBlock": hex(b),
            "toBlock": hex(to_b),
        }])
        if isinstance(logs, list):
            for lg in logs:
                topics = lg.get("topics", [])
                if len(topics) < 3:
                    continue
                frm = _topic_addr(topics[1])
                to = _topic_addr(topics[2])
                try:
                    val = int(lg.get("data", "0x0"), 16)
                except ValueError:
                    continue
                net[frm] -= val
                net[to] += val
        b = to_b + 1
        time.sleep(0.6)  # pace getLogs so we don't exhaust the RPC rate limit
    for junk in (ZERO, DEAD, token.lower()):
        net.pop(junk, None)
    return net


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
def run_discovery() -> List[Dict[str, Any]]:
    cfg = config.DISCOVERY
    print("\n=== Smart-Money Discovery (Base) ===\n")

    print("[1/5] Finding winner tokens (liquid + rising 24h) ...")
    winners, pool_addrs = fetch_winner_tokens()
    if not winners:
        print("\nNo winner tokens found. Try lowering DISCOVERY['min_price_change_h24'].")
        return []

    latest = smartmoney._hex_to_int(_rpc("eth_blockNumber", []))
    if not latest:
        print("\nCould not read latest block from RPC.")
        return []

    print(f"\n[2/5] Measuring net accumulation over last "
          f"{cfg['accum_window_blocks']} blocks across {len(winners)} winners ...")
    # addr -> {tokens: set, holds_now: int}; net is per (addr, token)
    accum: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"tokens": set()})
    addr_token_net: Dict[Tuple[str, str], int] = {}
    for w in winners:
        net = accumulate_token(w["token"], latest)
        # Top net accumulators of this token (exclude pools/LPs by net>0 already).
        top = sorted(((a, v) for a, v in net.items() if v > 0),
                     key=lambda kv: kv[1], reverse=True)[: cfg["accum_top_per_token"]]
        for a, v in top:
            if a in pool_addrs:
                continue  # skip known pools/LPs
            accum[a]["tokens"].add(w["token"])
            addr_token_net[(a, w["token"])] = v
        print(f"    - {w['name'][:30]:<30} (+{w['change_h24']:.0f}% 24h): "
              f"{len(top)} net accumulators")
        time.sleep(0.2)

    print("\n[3/5] Filtering CEX/MM/bots & infrastructure ...")
    dist = Counter(len(r["tokens"]) for r in accum.values())
    print(f"  unique accumulators: {len(accum)}")
    for k in sorted(dist, reverse=True):
        label = "token" if k == 1 else "tokens"
        print(f"    accumulated {k} distinct {label}: {dist[k]} wallet(s)")

    # Exclude obvious infrastructure (touches too many winners = router/CEX),
    # then BOUND the pool we RPC-check: multi-token accumulators first (best
    # signal), then top single-token accumulators by net size. Bounding keeps
    # the nonce/code/balance RPC load small enough to survive rate limits.
    eligible = [a for a, r in accum.items() if len(r["tokens"]) < cfg["infra_min_winners"]]
    eligible.sort(
        key=lambda a: (len(accum[a]["tokens"]),
                       max((addr_token_net.get((a, t), 0) for t in accum[a]["tokens"]),
                           default=0)),
        reverse=True)
    pool = eligible[: max(cfg["top_n"] * 4, 80)]
    if not pool:
        print("  No qualifying wallets found.")
        return []

    # Let the RPC rate limit recover after the heavy getLogs phase before we
    # hit it with nonce/code/balance batches.
    print("  cooling down before on-chain filtering ...")
    time.sleep(12)

    # Drop CEX hot wallets / MMs / bots by nonce FIRST (separates humans from
    # automation). Unknown nonce -> exclude (conservative).
    nonces = smartmoney.get_nonces(pool)
    print(f"  resolved nonces for {len(nonces)}/{len(pool)} wallet(s)")
    keep = [a for a in pool if a in nonces and nonces[a] <= cfg["max_nonce"]]
    print(f"  after nonce filter (<= {cfg['max_nonce']:,}): {len(keep)} of "
          f"{len(pool)} checked wallet(s) look human")
    if not keep:
        print("  No human-looking wallets found.")
        return []

    # Tag EOA vs smart-wallet; optionally drop big-bytecode contracts (routers).
    is_wallet = smartmoney.classify_wallets(keep)
    codes = smartmoney.get_codes(keep) if hasattr(smartmoney, "get_codes") else {}
    if cfg["drop_big_contracts"]:
        before = len(keep)
        keep = [a for a in keep
                if is_wallet.get(a, False) or len((codes.get(a) or "0x")) <= 1200]
        if before != len(keep):
            print(f"  dropped {before - len(keep)} large-bytecode contract(s)")
    if not cfg["include_smart_wallets"]:
        keep = [a for a in keep if is_wallet.get(a, False)]
    if not keep:
        print("  No qualifying wallets found.")
        return []

    # Step 4: conviction -- still holds an accumulated winner?
    still_holds: Dict[str, int] = defaultdict(int)
    if cfg["verify_holdings"]:
        print("\n[4/5] Verifying candidates still hold accumulated winners ...")
        pairs: List[tuple] = []
        index: List[Tuple[str, str]] = []
        for a in keep:
            for tok in accum[a]["tokens"]:
                pairs.append((tok, a))
                index.append((a, tok))
        balances = smartmoney.batch_token_balances(pairs)
        for (a, _tok), bal in zip(index, balances):
            if bal > config.SMART_MONEY_DUST_FLOOR:
                still_holds[a] += 1
        holders = [a for a in keep if still_holds.get(a, 0) > 0]
        print(f"  {len(holders)} of {len(keep)} still hold an accumulated winner")
        if holders:
            keep = holders
        else:
            print("  [info] no confirmed holders; showing all (lower confidence)")
    else:
        print("\n[4/5] Holdings verification disabled.")

    # Step 5: rank -- holders first, then # tokens accumulated, then nonce-low.
    keep.sort(key=lambda a: (still_holds.get(a, 0), len(accum[a]["tokens"]),
                             -nonces.get(a, 0)), reverse=True)
    candidates = []
    for a in keep[: cfg["top_n"]]:
        candidates.append({
            "wallet": a,
            "type": "EOA" if is_wallet.get(a, False) else "smart-wallet",
            "distinct_tokens": len(accum[a]["tokens"]),
            "still_holds": still_holds.get(a, 0),
            "nonce": nonces.get(a, 0),
            "tokens": sorted(accum[a]["tokens"]),
        })

    print(f"\n[5/5] {len(candidates)} candidate wallet(s) found.\n")
    _print_candidates(candidates)
    _save_candidates(candidates, cfg["out_file"])
    return candidates


def _print_candidates(candidates: List[Dict[str, Any]]) -> None:
    header = (f"{'#':>2}  {'WALLET':<44}{'TYPE':<14}{'TOKENS':>7}{'HOLD':>5}{'NONCE':>8}")
    print(header)
    print("-" * len(header))
    for i, c in enumerate(candidates, 1):
        print(f"{i:>2}  {c['wallet']:<44}{c['type']:<14}{c['distinct_tokens']:>7}"
              f"{c['still_holds']:>5}{c['nonce']:>8}")
    print("\n  TOKENS = distinct winner tokens net-accumulated   HOLD = still held now")
    print("  TYPE = EOA or smart-wallet   NONCE = tx count (low = not a bot/CEX)")
    print("  Review a wallet on https://debank.com/profile/<address> before trusting it.\n")


def _save_candidates(candidates: List[Dict[str, Any]], path: str) -> None:
    lines = ["# Smart-money candidate wallets (review before use!)",
             "# Paste good ones into config.SMART_MONEY_WALLETS", ""]
    for c in candidates:
        lines.append(
            f'"{c["wallet"]}",  # {c["type"]}, accumulated {c["distinct_tokens"]} winner(s), '
            f'{c["still_holds"]} still held, nonce {c["nonce"]:,}')
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Candidates written to: {path}")


if __name__ == "__main__":
    run_discovery()
