"""
Smart-money layer for the Base Utility Screener (Phase 2).

Idea: fundamentals prove *utility*; smart money confirms *conviction/timing*.
For each candidate token we check how many wallets from a curated, proven
seed list (config.SMART_MONEY_WALLETS) currently hold it.

This is fully KEYLESS: it reads on-chain balances via the official Base public
RPC using batched `eth_call` (ERC-20 balanceOf). No indexer / API key needed.

>>> The seed wallet list is the edge. <<<  A token held by several wallets
with a proven Base track record is a much stronger signal than one held by
none. Curate the list in config.py over time.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import requests

import config

# ERC-20 function selectors
_SEL_BALANCE_OF = "0x70a08231"
_SEL_DECIMALS = "0x313ce567"

_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


# --------------------------------------------------------------------------
# Token address resolution
# --------------------------------------------------------------------------
def resolve_base_token(address_field: Any) -> Optional[str]:
    """
    Turn a DefiLlama 'address' field into a usable Base token address.

    DefiLlama stores addresses like "base:0x..", "ethereum:0x..", or a bare
    "0x..". We only trust:
      - "base:0x.."  -> the Base address (correct)
      - bare "0x.."  -> assumed Base (true for Base-native protocols)
    An address prefixed with a *different* chain is that token's address on
    THAT chain and would be wrong against the Base RPC, so we skip it.
    """
    if not isinstance(address_field, str) or not address_field.strip():
        return None
    s = address_field.strip()
    if ":" in s:
        chain, _, addr = s.partition(":")
        if chain.lower() != config.CHAIN_SLUG:
            return None
        s = addr.strip()
    return s if _ADDR_RE.match(s) else None


# --------------------------------------------------------------------------
# Minimal JSON-RPC client (batched)
# --------------------------------------------------------------------------
def _rpc_batch(calls: List[Dict[str, str]]) -> List[Optional[str]]:
    """
    Execute a batch of eth_call requests. `calls` is a list of {to, data}.
    Returns results ordered to match `calls` (None on per-call error).
    """
    if not calls:
        return []
    payload = [
        {"jsonrpc": "2.0", "id": i, "method": "eth_call", "params": [c, "latest"]}
        for i, c in enumerate(calls)
    ]
    try:
        resp = requests.post(
            config.BASE_RPC_URL,
            json=payload,
            headers={"User-Agent": config.USER_AGENT, "Content-Type": "application/json"},
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 - fail soft
        print(f"  [warn] RPC batch failed: {exc}")
        return [None] * len(calls)

    if not isinstance(data, list):
        return [None] * len(calls)
    out: List[Optional[str]] = [None] * len(calls)
    for item in data:
        idx = item.get("id")
        if isinstance(idx, int) and 0 <= idx < len(calls):
            out[idx] = item.get("result")
    return out


def _balance_call(token: str, wallet: str) -> Dict[str, str]:
    data = _SEL_BALANCE_OF + wallet[2:].lower().rjust(64, "0")
    return {"to": token, "data": data}


def _hex_to_int(h: Optional[str]) -> int:
    if not h or not isinstance(h, str) or not h.startswith("0x") or h == "0x":
        return 0
    try:
        return int(h, 16)
    except ValueError:
        return 0


def _get_decimals(token: str) -> int:
    res = _rpc_batch([{"to": token, "data": _SEL_DECIMALS}])
    d = _hex_to_int(res[0]) if res else 0
    return d if 0 < d <= 36 else 18  # sane default


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def assess_token_smart_money(token_address: str, wallets: List[str]) -> Dict[str, Any]:
    """
    Check how many seed wallets hold `token_address`.
    Returns holders count, the holding wallets, and a 0-100 score.
    """
    if not wallets:
        return {"score": None, "holders": 0, "holder_wallets": [], "note": "no seed wallets"}

    calls = [_balance_call(token_address, w) for w in wallets]
    results = _rpc_batch(calls)

    holders: List[str] = []
    for wallet, raw in zip(wallets, results):
        if _hex_to_int(raw) > config.SMART_MONEY_DUST_FLOOR:
            holders.append(wallet)

    saturation = max(1, config.SMART_MONEY_SATURATION)
    score = round(min(1.0, len(holders) / saturation) * 100.0, 2)
    return {
        "score": score,
        "holders": len(holders),
        "holder_wallets": holders,
        "tracked": len(wallets),
    }


def enrich_smart_money(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Attach 'smart_money_score' + 'smart_money' detail to each candidate.

    If no seed wallets are configured, the pillar is disabled (score=None) and
    the composite scorer will renormalise over the remaining pillars.
    """
    wallets = [w.strip() for w in config.SMART_MONEY_WALLETS if _ADDR_RE.match(w.strip() or "")]
    if not wallets:
        print("  [info] smart-money: no seed wallets configured -> pillar disabled "
              "(curate config.SMART_MONEY_WALLETS to enable)")
        for c in candidates:
            c["smart_money_score"] = None
            c["smart_money"] = {"note": "no seed wallets configured"}
        return candidates

    resolved = 0
    for c in candidates:
        token = resolve_base_token(c.get("address"))
        if not token:
            c["smart_money_score"] = None
            c["smart_money"] = {"note": "no resolvable Base token address"}
            continue
        detail = assess_token_smart_money(token, wallets)
        c["smart_money_score"] = detail.get("score")
        c["smart_money"] = {**detail, "token": token}
        resolved += 1
    print(f"  [ok] smart-money: checked {resolved} token(s) against "
          f"{len(wallets)} seed wallet(s)")
    return candidates
