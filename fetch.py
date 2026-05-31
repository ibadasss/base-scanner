"""
Data fetching layer for the Base Utility Screener.

Sources (all free / keyless):
  - DefiLlama  : protocol TVL, market cap, fees & revenue on Base
  - GeckoTerminal : trending / new DEX pools on Base (token-level signals)

Every fetcher fails soft: on network errors it returns an empty result and
logs a warning instead of crashing the whole run.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

import requests

import config


def _get(url: str, params: Dict[str, Any] | None = None) -> Any | None:
    """GET with retries and a timeout. Returns parsed JSON or None."""
    headers = {"User-Agent": config.USER_AGENT, "Accept": "application/json"}
    last_err: Exception | None = None
    for attempt in range(1, config.REQUEST_RETRIES + 1):
        try:
            resp = requests.get(
                url, params=params, headers=headers, timeout=config.REQUEST_TIMEOUT
            )
            if resp.status_code == 429:
                # Rate limited -> back off and retry.
                wait = attempt * 2
                print(f"  [warn] 429 rate-limited on {url}, backing off {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001 - we intentionally fail soft
            last_err = exc
            time.sleep(attempt)  # linear backoff
    print(f"  [warn] failed to fetch {url}: {last_err}")
    return None


# --------------------------------------------------------------------------
# DefiLlama: protocols (TVL + market cap)
# --------------------------------------------------------------------------
def fetch_base_protocols() -> List[Dict[str, Any]]:
    """Return DefiLlama protocols that are active on Base, normalised."""
    data = _get(config.DEFILLAMA_PROTOCOLS)
    if not data:
        return []

    out: List[Dict[str, Any]] = []
    for p in data:
        chains = p.get("chains") or []
        if config.CHAIN_NAME not in chains:
            continue

        # Prefer Base-specific TVL when available, else total TVL.
        chain_tvls = p.get("chainTvls") or {}
        base_tvl = chain_tvls.get(config.CHAIN_NAME)
        tvl = base_tvl if isinstance(base_tvl, (int, float)) else p.get("tvl")

        out.append(
            {
                "name": p.get("name"),
                "symbol": (p.get("symbol") or "").upper(),
                "category": p.get("category"),
                "tvl": float(tvl) if isinstance(tvl, (int, float)) else 0.0,
                "mcap": float(p["mcap"]) if isinstance(p.get("mcap"), (int, float)) else None,
                "change_1d": p.get("change_1d"),
                "change_7d": p.get("change_7d"),
                "url": p.get("url"),
                "twitter": p.get("twitter"),
                "address": p.get("address"),  # may be "base:0x..." or None
            }
        )
    print(f"  [ok] DefiLlama protocols on Base: {len(out)}")
    return out


# --------------------------------------------------------------------------
# DefiLlama: fees & revenue overview for Base
# --------------------------------------------------------------------------
def _fetch_fees(data_type: str) -> Dict[str, Dict[str, Any]]:
    """data_type is 'dailyFees' or 'dailyRevenue'. Keyed by protocol name."""
    url = config.DEFILLAMA_FEES.format(chain=config.CHAIN_SLUG)
    params = {
        "excludeTotalDataChart": "true",
        "excludeTotalDataChartBreakdown": "true",
        "dataType": data_type,
    }
    data = _get(url, params)
    if not data:
        return {}

    result: Dict[str, Dict[str, Any]] = {}
    for p in data.get("protocols", []) or []:
        name = p.get("name")
        if not name:
            continue
        result[name] = {
            "total24h": p.get("total24h"),
            "total7d": p.get("total7d"),
            "total30d": p.get("total30d"),
            "totalAllTime": p.get("totalAllTime"),
        }
    return result


def fetch_base_fees_revenue() -> Dict[str, Dict[str, Any]]:
    """Merge fees + revenue per protocol name into one lookup table."""
    fees = _fetch_fees("dailyFees")
    revenue = _fetch_fees("dailyRevenue")
    merged: Dict[str, Dict[str, Any]] = {}
    names = set(fees) | set(revenue)
    for name in names:
        f = fees.get(name, {})
        r = revenue.get(name, {})
        merged[name] = {
            "fees_24h": f.get("total24h"),
            "fees_7d": f.get("total7d"),
            "fees_30d": f.get("total30d"),
            "revenue_24h": r.get("total24h"),
            "revenue_30d": r.get("total30d"),
        }
    print(f"  [ok] DefiLlama fee/revenue entries on Base: {len(merged)}")
    return merged


# --------------------------------------------------------------------------
# GeckoTerminal: trending / new pools (token-level discovery, phase-2 ready)
# --------------------------------------------------------------------------
def fetch_new_pools(pages: int = 1) -> List[Dict[str, Any]]:
    """Return newly created DEX pools on Base with basic liquidity/volume."""
    out: List[Dict[str, Any]] = []
    for page in range(1, pages + 1):
        url = f"{config.GECKOTERMINAL_BASE}/networks/{config.CHAIN_SLUG}/new_pools"
        data = _get(url, {"page": page})
        if not data:
            break
        for item in data.get("data", []) or []:
            attr = item.get("attributes", {}) or {}
            rel = item.get("relationships", {}) or {}
            base_token = (
                rel.get("base_token", {}).get("data", {}) or {}
            ).get("id", "")
            out.append(
                {
                    "pool_name": attr.get("name"),
                    "base_token_id": base_token,  # e.g. "base_0xabc..."
                    "price_usd": attr.get("base_token_price_usd"),
                    "liquidity_usd": attr.get("reserve_in_usd"),
                    "volume_24h": (attr.get("volume_usd") or {}).get("h24"),
                    "created_at": attr.get("pool_created_at"),
                }
            )
        time.sleep(1)  # be polite to the free API
    print(f"  [ok] GeckoTerminal new pools on Base: {len(out)}")
    return out
