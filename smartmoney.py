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
import time
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
def _coerce_id(raw: Any) -> Optional[int]:
    """Coerce a JSON-RPC response id to int. Some Base RPC backends return it
    as a string (e.g. "3"), which previously caused results to be dropped."""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _post_batch(payload: List[Dict[str, Any]]) -> Dict[int, Any]:
    """
    POST a JSON-RPC batch with retries, backoff, and ENDPOINT ROTATION,
    returning {int_id: result}.

    Robust against any single public RPC rate-limiting under load: it rotates
    through config.BASE_RPC_URLS. Returns the best-effort map; callers MUST
    treat a missing id as 'unknown' (and exclude conservatively) rather than as
    a 0/false value -- that silent-default behaviour previously let bots slip
    through filters.
    """
    endpoints = getattr(config, "BASE_RPC_URLS", [config.BASE_RPC_URL])
    best: Dict[int, Any] = {}
    attempt = 0
    for url in endpoints:
        for _ in range(2):  # two tries per endpoint before rotating
            attempt += 1
            try:
                resp = requests.post(
                    url, json=payload,
                    headers={"User-Agent": config.USER_AGENT,
                             "Content-Type": "application/json"},
                    timeout=config.REQUEST_TIMEOUT,
                )
                if resp.status_code == 429:
                    time.sleep(2 * attempt)
                    continue
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, list):
                    got: Dict[int, Any] = {}
                    for item in data:
                        cid = _coerce_id(item.get("id"))
                        if cid is not None and "result" in item:
                            got[cid] = item["result"]
                    if len(got) > len(best):
                        best = got
                    if len(got) == len(payload):
                        return got  # complete
                time.sleep(1.0)
            except Exception:  # noqa: BLE001 - rotate to next endpoint
                time.sleep(1.0)
                break
    return best


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
    got = _post_batch(payload)
    return [got.get(i) for i in range(len(calls))]


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


def _code_is_wallet(code: Optional[str]) -> bool:
    """
    True if an address is a usable wallet (EOA), not a contract.

    - "0x"          -> plain EOA
    - "0xef0100..." -> EIP-7702 delegated EOA (still a user wallet)
    - anything else -> real contract bytecode (router/aggregator/MM) -> exclude
    """
    if code is None:
        return False
    c = code.lower()
    if c in ("0x", "0x0", ""):
        return True
    return c.startswith("0xef0100")


def classify_wallets(addresses: List[str]) -> Dict[str, bool]:
    """
    Returns {address: is_wallet} for addresses whose code could be resolved.
    Addresses that could not be resolved are OMITTED (callers should treat a
    missing entry as 'unknown' and decide conservatively).
    """
    return {a: _code_is_wallet(code) for a, code in get_codes(addresses).items()}


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



def batch_token_balances(pairs: List[tuple], chunk: int = 60) -> List[int]:
    """
    Resolve raw balances for many (token, wallet) pairs via batched eth_call.
    Returns a list of ints aligned to `pairs`. Used to verify that discovered
    buyers still HOLD the tokens they bought (conviction check).
    """
    out: List[int] = []
    for start in range(0, len(pairs), chunk):
        sub = pairs[start:start + chunk]
        calls = [_balance_call(token, wallet) for token, wallet in sub]
        results = _rpc_batch(calls)
        out.extend(_hex_to_int(r) for r in results)
    return out



def get_nonces(addresses: List[str], batch_size: int = 50) -> Dict[str, int]:
    """
    Batch eth_getTransactionCount -> {address: nonce} for RESOLVED addresses
    only. A very high nonce flags CEX hot wallets / market makers / bots.
    Unresolved addresses are omitted (treat as unknown, exclude conservatively).
    """
    out: Dict[str, int] = {}
    uniq = list(dict.fromkeys(a for a in addresses if _ADDR_RE.match(a or "")))
    for start in range(0, len(uniq), batch_size):
        chunk = uniq[start:start + batch_size]
        payload = [
            {"jsonrpc": "2.0", "id": i, "method": "eth_getTransactionCount",
             "params": [addr, "latest"]}
            for i, addr in enumerate(chunk)
        ]
        got = _post_batch(payload)
        for i, addr in enumerate(chunk):
            if i in got:
                out[addr] = _hex_to_int(got[i])
    return out



def get_codes(addresses: List[str], batch_size: int = 50) -> Dict[str, str]:
    """
    Batch eth_getCode -> {address: code_hex} for RESOLVED addresses only.
    Lets callers measure bytecode size (large bytecode = router/aggregator).
    """
    out: Dict[str, str] = {}
    uniq = list(dict.fromkeys(a for a in addresses if _ADDR_RE.match(a or "")))
    for start in range(0, len(uniq), batch_size):
        chunk = uniq[start:start + batch_size]
        payload = [
            {"jsonrpc": "2.0", "id": i, "method": "eth_getCode", "params": [addr, "latest"]}
            for i, addr in enumerate(chunk)
        ]
        got = _post_batch(payload)
        for i, addr in enumerate(chunk):
            if i in got:
                out[addr] = got[i] or "0x"
    return out
