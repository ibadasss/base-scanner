"""
Social signal layer for the Base Utility Screener (Phase 2).

Base has a native social graph (Farcaster) that other chains don't. Organic
Farcaster buzz often *precedes* on-chain moves, so it's a useful early-
detection signal -- especially "strong fundamentals + rising buzz + not yet
mainstream".

This uses the KEYLESS Warpcast public search API. If a Neynar API key is
provided it will be used instead for richer results.

For each protocol we:
  1. search recent casts mentioning it
  2. keep only casts within the lookback window
  3. measure volume (# casts) + engagement (reactions/recasts/replies/quotes)
Raw buzz is then log-normalised across the candidate set to a 0-100 score.
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List

import requests

import config


def _now_ms() -> int:
    return int(time.time() * 1000)


def _engagement(cast: Dict[str, Any]) -> int:
    def _c(key: str) -> int:
        v = cast.get(key)
        if isinstance(v, dict):
            return int(v.get("count") or 0)
        return int(v or 0) if isinstance(v, (int, float)) else 0

    return _c("reactions") + _c("recasts") + _c("replies") + int(cast.get("quoteCount") or 0)


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------
def _search_warpcast(query: str, limit: int) -> List[Dict[str, Any]]:
    try:
        resp = requests.get(
            config.WARPCAST_SEARCH,
            params={"q": query, "limit": min(limit, 100)},
            headers={"User-Agent": config.USER_AGENT},
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("result", {}).get("casts", []) or []
    except Exception as exc:  # noqa: BLE001 - fail soft
        print(f"  [warn] Warpcast search failed for '{query}': {exc}")
        return []


def _search_neynar(query: str, limit: int) -> List[Dict[str, Any]]:
    try:
        resp = requests.get(
            config.NEYNAR_SEARCH,
            params={"q": query, "limit": min(limit, 100)},
            headers={"User-Agent": config.USER_AGENT, "api_key": config.NEYNAR_API_KEY},
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        casts = resp.json().get("result", {}).get("casts", []) or []
        # Normalise Neynar shape -> the few fields we use.
        norm = []
        for c in casts:
            reactions = c.get("reactions", {}) or {}
            norm.append({
                "timestamp": c.get("timestamp"),
                "reactions": {"count": len(reactions.get("likes", [])) if isinstance(reactions.get("likes"), list) else reactions.get("likes_count", 0)},
                "recasts": {"count": reactions.get("recasts_count", 0)},
                "replies": c.get("replies", {}),
                "text": c.get("text"),
            })
        return norm
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] Neynar search failed for '{query}': {exc}")
        return []


def _parse_ts(ts: Any) -> int:
    """Warpcast gives epoch ms (int). Neynar may give ISO strings."""
    if isinstance(ts, (int, float)):
        return int(ts)
    if isinstance(ts, str):
        try:
            from datetime import datetime, timezone

            return int(datetime.fromisoformat(ts.replace("Z", "+00:00"))
                       .replace(tzinfo=timezone.utc).timestamp() * 1000)
        except Exception:  # noqa: BLE001
            return 0
    return 0


# --------------------------------------------------------------------------
# Buzz measurement
# --------------------------------------------------------------------------
def measure_buzz(query: str) -> Dict[str, Any]:
    """Return {recent_casts, engagement, raw_buzz} for a search query."""
    if config.NEYNAR_API_KEY:
        casts = _search_neynar(query, config.SOCIAL_MAX_CASTS)
    else:
        casts = _search_warpcast(query, config.SOCIAL_MAX_CASTS)

    cutoff = _now_ms() - config.SOCIAL_LOOKBACK_DAYS * 86_400_000
    recent_casts = 0
    engagement = 0
    for c in casts:
        if _parse_ts(c.get("timestamp")) >= cutoff:
            recent_casts += 1
            engagement += _engagement(c)

    # Volume + dampened engagement. Log-normalised across candidates later.
    raw_buzz = recent_casts + 0.5 * math.log1p(engagement)
    return {"recent_casts": recent_casts, "engagement": engagement, "raw_buzz": raw_buzz}


def _normalise_log(values: List[float]) -> List[float]:
    if not values:
        return []
    damped = [math.log1p(max(v, 0.0)) for v in values]
    lo, hi = min(damped), max(damped)
    if hi - lo < 1e-9:
        return [0.0 for _ in damped]
    return [(v - lo) / (hi - lo) for v in damped]


def enrich_social(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Attach 'social_score' (0-100) + 'social' detail to each candidate, based on
    recent Farcaster buzz for the protocol name.
    """
    provider = "Neynar" if config.NEYNAR_API_KEY else "Warpcast (keyless)"
    print(f"  [info] social: measuring Farcaster buzz via {provider} ...")

    raw_values: List[float] = []
    for c in candidates:
        query = (c.get("name") or "").strip()
        if not query:
            c["_buzz"] = {"recent_casts": 0, "engagement": 0, "raw_buzz": 0.0}
            raw_values.append(0.0)
            continue
        buzz = measure_buzz(query)
        c["_buzz"] = buzz
        raw_values.append(buzz["raw_buzz"])
        time.sleep(0.3)  # be polite to the public API

    norm = _normalise_log(raw_values)
    for c, n in zip(candidates, norm):
        buzz = c.pop("_buzz")
        c["social_score"] = round(n * 100.0, 2)
        c["social"] = buzz
    print(f"  [ok] social: scored {len(candidates)} candidate(s)")
    return candidates
