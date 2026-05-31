"""
Utility scoring layer for the Base Utility Screener.

Turns raw protocol metrics into a 0-100 "utility score" using the rubric
we agreed on:

    value       (35) : annualised fees / market cap  -> cheap vs. earnings
    stickiness  (25) : TVL trend not bleeding out     -> sticky liquidity
    activity    (25) : fee/volume momentum            -> real usage
    scale       (15) : absolute TVL footprint         -> established enough

Each component is normalised to 0-1 across the candidate set, then weighted.
A high score with a LOW market cap / low public attention is the retail
sweet spot: strong fundamentals that the crowd has not priced in yet.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List

import config


def _safe(x: Any) -> float:
    return float(x) if isinstance(x, (int, float)) and not math.isnan(float(x)) else 0.0


def _normalise(values: List[float]) -> List[float]:
    """Min-max normalise a list to 0-1. Log-dampen heavy tails first."""
    if not values:
        return []
    damped = [math.log1p(max(v, 0.0)) for v in values]
    lo, hi = min(damped), max(damped)
    if hi - lo < 1e-9:
        return [0.0 for _ in damped]
    return [(v - lo) / (hi - lo) for v in damped]


def compute_scores(protocols: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach 'utility_score' and component breakdowns to each protocol."""
    if not protocols:
        return []

    # --- Raw component signals -------------------------------------------
    value_raw: List[float] = []      # annualised fees / mcap
    stickiness_raw: List[float] = [] # higher = healthier TVL trend
    activity_raw: List[float] = []   # fees_7d annualised vs fees_30d run-rate
    scale_raw: List[float] = []      # TVL

    for p in protocols:
        fees_30d = _safe(p.get("fees_30d"))
        fees_7d = _safe(p.get("fees_7d"))
        rev_30d = _safe(p.get("revenue_30d"))
        mcap = _safe(p.get("mcap"))
        tvl = _safe(p.get("tvl"))
        change_7d = p.get("change_7d")

        # value: annualised EARNINGS / mcap (a real earnings yield).
        # Prefer protocol revenue (its actual take) over gross fees; fall back
        # to fees when revenue is missing. Only meaningful when mcap is known.
        earnings_basis = rev_30d if rev_30d > 0 else fees_30d
        annual_earnings = earnings_basis * 12.0
        value_raw.append((annual_earnings / mcap) if mcap > 0 else 0.0)

        # stickiness: map 7d TVL change (%) into a positive-ish signal.
        # -100% -> 0, 0% -> ~1, growth caps out so we don't reward pump noise.
        c = change_7d if isinstance(change_7d, (int, float)) else 0.0
        stickiness_raw.append(max(0.0, min(1.0, (c + 100.0) / 100.0)))

        # activity: is recent (7d) fee run-rate keeping pace with 30d avg?
        run_rate_30d = fees_30d / 30.0 if fees_30d else 0.0
        run_rate_7d = fees_7d / 7.0 if fees_7d else 0.0
        momentum = (run_rate_7d / run_rate_30d) if run_rate_30d > 0 else 0.0
        # blend absolute fee size with momentum so tiny protocols don't win on ratio alone
        activity_raw.append(fees_7d * max(momentum, 0.0))

        scale_raw.append(tvl)

    # --- Normalise --------------------------------------------------------
    value_n = _normalise(value_raw)
    # stickiness is already 0-1; keep as-is
    stickiness_n = stickiness_raw
    activity_n = _normalise(activity_raw)
    scale_n = _normalise(scale_raw)

    w = config.UTILITY_WEIGHTS
    out: List[Dict[str, Any]] = []
    for i, p in enumerate(protocols):
        comp = {
            "value": round(value_n[i] * w["value"], 2),
            "stickiness": round(stickiness_n[i] * w["stickiness"], 2),
            "activity": round(activity_n[i] * w["activity"], 2),
            "scale": round(scale_n[i] * w["scale"], 2),
        }
        score = round(sum(comp.values()), 2)

        # Headline "is it cheap?" ratios worth surfacing in the report.
        mcap = _safe(p.get("mcap"))
        annual_fees = _safe(p.get("fees_30d")) * 12.0
        rev_30d = _safe(p.get("revenue_30d"))
        annual_rev = (rev_30d if rev_30d > 0 else _safe(p.get("fees_30d"))) * 12.0
        fees_to_mcap = round(annual_fees / mcap, 4) if mcap > 0 else None
        earnings_yield = round(annual_rev / mcap, 4) if mcap > 0 else None

        out.append({**p, "utility_score": score, "components": comp,
                    "annual_fees_to_mcap": fees_to_mcap,
                    "earnings_yield": earnings_yield})

    out.sort(key=lambda x: x["utility_score"], reverse=True)
    return out


def compute_composite(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Blend the three pillars into a final 0-100 'composite_score':

        utility      (always present)
        smart_money  (None if no seed wallets / no resolvable address)
        social       (Farcaster buzz)

    Weights (config.COMPOSITE_WEIGHTS) are renormalised PER CANDIDATE over only
    the pillars that have data, so a token missing smart-money or social data
    is scored fairly on what we do know rather than being penalised.
    """
    w = config.COMPOSITE_WEIGHTS
    for c in candidates:
        pillars = {
            "utility": c.get("utility_score"),
            "smart_money": c.get("smart_money_score"),
            "social": c.get("social_score"),
        }
        available = {k: float(v) for k, v in pillars.items()
                     if isinstance(v, (int, float))}
        weight_sum = sum(w[k] for k in available) or 1.0
        composite = sum(val * w[k] for k, val in available.items()) / weight_sum
        c["composite_score"] = round(composite, 2)
        c["pillars_used"] = sorted(available.keys())

    candidates.sort(key=lambda x: x.get("composite_score", 0.0), reverse=True)
    return candidates
