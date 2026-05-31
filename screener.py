"""
Base Utility Screener -- main entrypoint.

Layered pipeline (the design we agreed on):

    1. FETCH    : pull Base protocols + fees/revenue from DefiLlama
    2. SAFETY   : drop scams / dead / illiquid protocols (data-quality filter)
    3. SCORE    : rank survivors by utility (revenue, stickiness, usage, scale)
    4. ENRICH   : for the top candidates, add smart-money + Farcaster signals
                  and blend everything into a composite score (Phase 2)
    5. REPORT   : print top candidates + export full ranking to CSV

Usage:
    python screener.py                      # top 25, enriched -> console + CSV
    python screener.py --top 10             # show top 10
    python screener.py --csv out.csv        # custom CSV path
    python screener.py --no-enrich          # fundamentals only (fast)
    python screener.py --enrich-top 20      # enrich the top 20 candidates
    python screener.py --show-rejected      # explain safety-filter rejections
    python screener.py --check 0x..         # token-level safety check on an address
"""
from __future__ import annotations

import argparse
import csv
import json
from typing import Any, Dict, List

import config
import fetch
import safety
import score
import smartmoney
import social
import discover


def _merge_fees(
    protocols: List[Dict[str, Any]], fees: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Attach fee/revenue metrics onto each protocol by name match."""
    for p in protocols:
        info = fees.get(p["name"])
        if info:
            p.update(info)
    return protocols


def run_screener(top_n: int, csv_path: str, enrich: bool = True,
                 enrich_top: int = config.ENRICH_TOP_N,
                 show_rejected: bool = False) -> None:
    print("\n=== Base Utility Screener ===\n")

    print("[1/5] Fetching data from DefiLlama ...")
    protocols = fetch.fetch_base_protocols()
    fees = fetch.fetch_base_fees_revenue()
    if not protocols:
        print("\nNo protocol data fetched. Check your network connection.")
        return
    protocols = _merge_fees(protocols, fees)

    print("\n[2/5] Applying safety / data-quality filter ...")
    kept, rejected = safety.apply_quality_filter(protocols)
    if not kept:
        print("\nNo protocols passed the safety filter. Try loosening thresholds in config.py.")
        return

    print("\n[3/5] Scoring utility (fundamentals) ...")
    ranked = score.compute_scores(kept)

    enriched = False
    if enrich and ranked:
        print(f"\n[4/5] Enriching top {min(enrich_top, len(ranked))} with "
              f"smart-money + Farcaster signals ...")
        head = ranked[:enrich_top]
        tail = ranked[enrich_top:]
        smartmoney.enrich_smart_money(head)
        social.enrich_social(head)
        score.compute_composite(head)   # re-ranks head by composite score
        ranked = head + tail            # enriched winners first, then the rest
        enriched = True
    else:
        print("\n[4/5] Skipping enrichment (fundamentals only).")

    print("\n[5/5] Building report ...\n")
    _print_table(ranked[:top_n], enriched)
    _export_csv(ranked, csv_path)
    print(f"\nFull ranking ({len(ranked)} protocols) written to: {csv_path}")
    print(f"Rejected by safety filter: {len(rejected)} "
          f"(run with --show-rejected to see why)\n")

    if show_rejected:
        _print_rejected(rejected)


def _fmt_usd(x: Any) -> str:
    if not isinstance(x, (int, float)):
        return "-"
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(x) >= div:
            return f"${x / div:.1f}{unit}"
    return f"${x:.0f}"


def _fmt_score(x: Any) -> str:
    return f"{x:.1f}" if isinstance(x, (int, float)) else "-"


def _print_table(rows: List[Dict[str, Any]], enriched: bool) -> None:
    header = (f"{'#':>2}  {'PROTOCOL':<22}{'CAT':<13}"
              f"{'COMP':>6}{'UTIL':>6}{'SM':>6}{'SOC':>6}  "
              f"{'TVL':>8}  {'FEES30D':>8}")
    print(header)
    print("-" * len(header))
    for i, r in enumerate(rows, 1):
        print(
            f"{i:>2}  {(r.get('name') or '')[:21]:<22}"
            f"{(r.get('category') or '')[:12]:<13}"
            f"{_fmt_score(r.get('composite_score')):>6}"
            f"{_fmt_score(r.get('utility_score')):>6}"
            f"{_fmt_score(r.get('smart_money_score')):>6}"
            f"{_fmt_score(r.get('social_score')):>6}  "
            f"{_fmt_usd(r.get('tvl')):>8}  "
            f"{_fmt_usd(r.get('fees_30d')):>8}"
        )
    if enriched:
        print("\n  COMP=composite  UTIL=utility(fundamentals)  "
              "SM=smart-money  SOC=Farcaster social  ('-' = no data)")


def _export_csv(rows: List[Dict[str, Any]], path: str) -> None:
    fields = [
        "rank", "name", "symbol", "category",
        "composite_score", "utility_score", "smart_money_score", "social_score",
        "pillars_used",
        "tvl", "mcap", "fees_30d", "fees_7d", "revenue_30d",
        "annual_fees_to_mcap", "earnings_yield", "change_7d", "url", "twitter",
        "score_value", "score_stickiness", "score_activity", "score_scale",
        "sm_holders", "sm_tracked", "social_recent_casts", "social_engagement",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for i, r in enumerate(rows, 1):
            comp = r.get("components", {})
            sm = r.get("smart_money", {}) or {}
            soc = r.get("social", {}) or {}
            writer.writerow({
                "rank": i,
                "name": r.get("name"),
                "symbol": r.get("symbol"),
                "category": r.get("category"),
                "composite_score": r.get("composite_score"),
                "utility_score": r.get("utility_score"),
                "smart_money_score": r.get("smart_money_score"),
                "social_score": r.get("social_score"),
                "pillars_used": "|".join(r.get("pillars_used", []) or []),
                "tvl": r.get("tvl"),
                "mcap": r.get("mcap"),
                "fees_30d": r.get("fees_30d"),
                "fees_7d": r.get("fees_7d"),
                "revenue_30d": r.get("revenue_30d"),
                "annual_fees_to_mcap": r.get("annual_fees_to_mcap"),
                "earnings_yield": r.get("earnings_yield"),
                "change_7d": r.get("change_7d"),
                "url": r.get("url"),
                "twitter": r.get("twitter"),
                "score_value": comp.get("value"),
                "score_stickiness": comp.get("stickiness"),
                "score_activity": comp.get("activity"),
                "score_scale": comp.get("scale"),
                "sm_holders": sm.get("holders"),
                "sm_tracked": sm.get("tracked"),
                "social_recent_casts": soc.get("recent_casts"),
                "social_engagement": soc.get("engagement"),
            })


def _print_rejected(rejected: List[Dict[str, Any]], limit: int = 40) -> None:
    """Show why protocols were filtered out (largest TVL first)."""
    rejected_sorted = sorted(
        rejected, key=lambda r: r.get("tvl") or 0.0, reverse=True
    )
    print(f"--- Rejected by safety filter (showing up to {limit}) ---")
    print(f"{'PROTOCOL':<26}{'TVL':>9}  REASON")
    print("-" * 70)
    for r in rejected_sorted[:limit]:
        print(
            f"{(r.get('name') or '')[:25]:<26}"
            f"{_fmt_usd(r.get('tvl')):>9}  "
            f"{r.get('reason', '')}"
        )
    if len(rejected_sorted) > limit:
        print(f"... and {len(rejected_sorted) - limit} more.")
    print()


def run_token_check(address: str) -> None:
    print(f"\n=== Token safety check: {address} (Base) ===\n")
    result = safety.assess_token(address)
    print(json.dumps(result, indent=2))
    verdict = "SAFE-ish" if result.get("safe") else "RISKY / UNKNOWN"
    print(f"\nVerdict: {verdict}")
    print("(Always DYOR -- this is a heuristic, not a guarantee.)\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Base Utility Screener")
    parser.add_argument("--top", type=int, default=config.DEFAULT_TOP_N,
                        help="number of top protocols to print")
    parser.add_argument("--csv", default=config.DEFAULT_CSV_PATH,
                        help="output CSV path")
    parser.add_argument("--no-enrich", action="store_true",
                        help="skip smart-money + social enrichment (fundamentals only)")
    parser.add_argument("--enrich-top", type=int, default=config.ENRICH_TOP_N,
                        help="how many top candidates to enrich")
    parser.add_argument("--check", metavar="ADDRESS",
                        help="run token-level safety check on a single address")
    parser.add_argument("--discover", action="store_true",
                        help="discover candidate smart-money wallets from on-chain activity")
    parser.add_argument("--show-rejected", action="store_true",
                        help="list protocols dropped by the safety filter and why")
    args = parser.parse_args()

    if args.check:
        run_token_check(args.check)
    elif args.discover:
        discover.run_discovery()
    else:
        run_screener(
            args.top, args.csv,
            enrich=not args.no_enrich,
            enrich_top=args.enrich_top,
            show_rejected=args.show_rejected,
        )


if __name__ == "__main__":
    main()
