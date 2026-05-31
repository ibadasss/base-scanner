"""
Safety / data-quality layer for the Base Utility Screener.

This runs FIRST, before scoring, because for retail the biggest edge is
*avoiding* rug pulls and dead projects rather than finding the next gem.

Two levels:
  1. Protocol-level data-quality filter (keyless): TVL floor, not bleeding
     out, real fee generation. Operates on DefiLlama data.
  2. Token-level safety checks (optional): honeypot.is + Etherscan contract
     verification for an individual token address. Used in phase-2 / on demand.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import requests

import config


# --------------------------------------------------------------------------
# 1. Protocol-level data-quality filter
# --------------------------------------------------------------------------
def passes_quality_filter(protocol: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Decide whether a protocol is trustworthy enough to score.
    Returns (passed, reason_if_rejected).
    """
    tvl = protocol.get("tvl") or 0.0
    if tvl < config.MIN_TVL_USD:
        return False, f"TVL ${tvl:,.0f} < min ${config.MIN_TVL_USD:,.0f}"

    change_7d = protocol.get("change_7d")
    if isinstance(change_7d, (int, float)) and change_7d < config.MAX_TVL_DROP_7D_PCT:
        return False, f"TVL crashed {change_7d:.1f}% in 7d (bleeding out)"

    fees_30d = protocol.get("fees_30d")
    if not isinstance(fees_30d, (int, float)) or fees_30d < config.MIN_FEES_30D_USD:
        return False, "no meaningful fee generation (no real utility/usage)"

    return True, ""


def apply_quality_filter(
    protocols: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split protocols into (kept, rejected). Rejected carry a 'reason' field."""
    kept: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for p in protocols:
        ok, reason = passes_quality_filter(p)
        if ok:
            kept.append(p)
        else:
            rejected.append({**p, "reason": reason})
    print(f"  [ok] safety filter: kept {len(kept)}, rejected {len(rejected)}")
    return kept, rejected


# --------------------------------------------------------------------------
# 2. Token-level safety checks (optional, address-based)
# --------------------------------------------------------------------------
def check_honeypot(token_address: str) -> Dict[str, Any]:
    """
    Query honeypot.is for an individual token on Base.
    Returns a normalised dict; on failure returns {'ok': None}.
    """
    try:
        resp = requests.get(
            config.HONEYPOT_API,
            params={"address": token_address, "chainID": config.CHAIN_ID},
            headers={"User-Agent": config.USER_AGENT},
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        hp = data.get("honeypotResult", {}) or {}
        tax = data.get("simulationResult", {}) or {}
        return {
            "ok": not hp.get("isHoneypot", False),
            "is_honeypot": hp.get("isHoneypot"),
            "buy_tax": tax.get("buyTax"),
            "sell_tax": tax.get("sellTax"),
            "reason": hp.get("honeypotReason"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": None, "error": str(exc)}


def _check_sourcify(token_address: str) -> Dict[str, Any]:
    """
    Keyless contract verification via Sourcify v2. Base (8453) is NOT on
    Etherscan's free tier, so Sourcify is our default verification source.
    A 200 with a 'match' field means the source is verified/published.
    """
    url = config.SOURCIFY_V2.format(chain_id=config.CHAIN_ID, address=token_address)
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": config.USER_AGENT, "Accept": "application/json"},
            timeout=config.REQUEST_TIMEOUT,
        )
        if resp.status_code == 404:
            return {"verified": False, "source": "sourcify"}
        resp.raise_for_status()
        data = resp.json()
        match = data.get("match") or data.get("runtimeMatch") or data.get("creationMatch")
        return {
            "verified": bool(match),
            "match": match,
            "verified_at": data.get("verifiedAt"),
            "source": "sourcify",
        }
    except Exception as exc:  # noqa: BLE001
        return {"verified": None, "source": "sourcify", "error": str(exc)}


def _check_etherscan(token_address: str) -> Dict[str, Any]:
    """Optional verification via Etherscan V2 (needs a Base-capable paid key)."""
    if not config.ETHERSCAN_API_KEY:
        return {"verified": None, "skipped": "no ETHERSCAN_API_KEY"}
    try:
        resp = requests.get(
            config.ETHERSCAN_V2,
            params={
                "chainid": config.CHAIN_ID,
                "module": "contract",
                "action": "getsourcecode",
                "address": token_address,
                "apikey": config.ETHERSCAN_API_KEY,
            },
            headers={"User-Agent": config.USER_AGENT},
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        if str(body.get("status")) != "1":
            # e.g. "Free API access is not supported for this chain"
            return {"verified": None, "source": "etherscan", "note": body.get("result")}
        result = (body.get("result") or [{}])[0]
        source = result.get("SourceCode") or ""
        return {
            "verified": bool(source),
            "contract_name": result.get("ContractName"),
            "source": "etherscan",
        }
    except Exception as exc:  # noqa: BLE001
        return {"verified": None, "source": "etherscan", "error": str(exc)}


def check_contract_verified(token_address: str) -> Dict[str, Any]:
    """
    Check whether a contract's source is verified.

    Strategy: Sourcify first (keyless, covers Base). If Sourcify can't confirm
    and an Etherscan key is configured, fall back to Etherscan.
    """
    sourcify = _check_sourcify(token_address)
    if sourcify.get("verified") is True:
        return sourcify
    if config.ETHERSCAN_API_KEY:
        etherscan = _check_etherscan(token_address)
        if etherscan.get("verified") is not None:
            return etherscan
    return sourcify


def assess_token(token_address: str) -> Dict[str, Any]:
    """Run all token-level safety checks for a single address."""
    honeypot = check_honeypot(token_address)
    verified = check_contract_verified(token_address)
    return {
        "address": token_address,
        "honeypot": honeypot,
        "verification": verified,
        "safe": honeypot.get("ok") is True and verified.get("verified") is not False,
    }
