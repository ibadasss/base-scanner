"""
Smart Money Monitor + Consensus Alert (Base).

Turns your curated SMART_MONEY_WALLETS list into a live early-detection radar:

  1. Scan recent on-chain ERC-20 Transfers INTO the smart-money wallets
     (eth_getLogs, topic-filtered on the recipient -> one query for all wallets).
  2. Group by token; count how many DISTINCT smart wallets freshly received it.
  3. CONSENSUS: a token received by >= MIN_CONSENSUS wallets in the window is a
     strong signal (multiple proven wallets buying the same thing).
  4. Filter spam/airdrops & scams: require real liquidity/volume (GeckoTerminal)
     and pass a honeypot check (honeypot.is).
  5. Alert to console + Telegram, with a per-token cooldown to avoid spam.

Fully keyless except Telegram (which only needs a free bot token). Run it on a
schedule (e.g. cron every 15-30 min); state is persisted between runs.
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from typing import Any, Dict, List, Set, Tuple

import requests

import alerts
import config
import discover     # reuse _rpc (endpoint rotation) + _get (GeckoTerminal backoff)
import safety
import smartmoney

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def _pad(addr: str) -> str:
    return "0x" + addr[2:].lower().rjust(64, "0")


def _load_state() -> Dict[str, Any]:
    path = config.MONITOR["state_file"]
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            pass
    return {"alerts": {}, "last_block": 0}


def _save_state(state: Dict[str, Any]) -> None:
    try:
        with open(config.MONITOR["state_file"], "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] could not save state: {exc}")


def fetch_incoming_transfers(wallets: List[str], from_block: int, to_block: int
                             ) -> List[Dict[str, str]]:
    """All ERC-20 Transfers whose recipient is one of `wallets`, over the range."""
    cfg = config.MONITOR
    topic_to = [_pad(w) for w in wallets]   # topics[2] OR-match = any of our wallets
    events: List[Dict[str, str]] = []
    b = from_block
    while b <= to_block:
        to_b = min(b + cfg["chunk_blocks"] - 1, to_block)
        logs = discover._rpc("eth_getLogs", [{
            "topics": [TRANSFER_TOPIC, None, topic_to],
            "fromBlock": hex(b),
            "toBlock": hex(to_b),
        }])
        if isinstance(logs, list):
            for lg in logs:
                topics = lg.get("topics", [])
                if len(topics) < 3:
                    continue
                events.append({
                    "token": (lg.get("address") or "").lower(),
                    "from": ("0x" + topics[1][-40:]).lower(),
                    "to": ("0x" + topics[2][-40:]).lower(),
                })
        b = to_b + 1
        time.sleep(0.4)
    return events


def token_info(address: str) -> Dict[str, Any] | None:
    """GeckoTerminal token info: symbol/name + liquidity/volume (spam filter)."""
    url = config.GECKOTERMINAL_TOKEN.format(chain=config.CHAIN_SLUG, address=address)
    data = None
    for attempt in range(2):  # lightweight: don't use the heavy 60s backoff path
        try:
            resp = requests.get(url, headers={"User-Agent": config.USER_AGENT,
                                              "Accept": "application/json"},
                                timeout=15)
            if resp.status_code == 429:
                time.sleep(2)
                continue
            resp.raise_for_status()
            data = resp.json()
            break
        except Exception:  # noqa: BLE001
            time.sleep(1)
    if not data:
        return None
    a = (data.get("data", {}) or {}).get("attributes", {}) or {}
    return {
        "symbol": a.get("symbol") or "?",
        "name": a.get("name") or "",
        "liq": float(a.get("total_reserve_in_usd") or 0),
        "vol24": float((a.get("volume_usd") or {}).get("h24") or 0),
        "price": a.get("price_usd"),
    }


def _format_alert(token: str, holders: Set[str], info: Dict[str, Any],
                  hp: Dict[str, Any]) -> str:
    tax = ""
    if isinstance(hp.get("buy_tax"), (int, float)) or isinstance(hp.get("sell_tax"), (int, float)):
        tax = f" (buy {hp.get('buy_tax', '?')}% / sell {hp.get('sell_tax', '?')}%)"
    price = f"${float(info['price']):.6g}" if info.get("price") else "?"
    warn = "\u26a0"
    hp_label = ("YES " + warn) if hp.get("is_honeypot") else "no"
    name_part = ("- " + info["name"]) if info["name"] else ""
    hours = max(1, config.MONITOR["lookback_blocks"] // 1800)
    return (
        "\U0001F525 <b>SMART MONEY CONSENSUS</b>\n\n"
        f"<b>${info['symbol']}</b> {name_part}\n"
        f"<b>{len(holders)} smart wallets</b> accumulated this in the last ~{hours}h\n\n"
        f"\U0001F4A7 Liquidity: ${info['liq']:,.0f}\n"
        f"\U0001F4CA Vol 24h: ${info['vol24']:,.0f}\n"
        f"\U0001F4B5 Price: {price}\n"
        f"\U0001F6E1 Honeypot: {hp_label}{tax}\n\n"
        f"<code>{token}</code>\n"
        f"\U0001F4C8 https://www.geckoterminal.com/base/tokens/{token}\n"
        f"\U0001F50E https://basescan.org/token/{token}\n\n"
        "<i>Research signal, not financial advice. DYOR.</i>"
    )


def run_monitor(send: bool = True) -> List[Dict[str, Any]]:
    cfg = config.MONITOR
    try:
        sys.stdout.reconfigure(line_buffering=True)  # show progress in real time
    except Exception:  # noqa: BLE001
        pass
    print("\n=== Smart Money Monitor (Base) ===\n")
    wallets = [w.strip().lower() for w in config.SMART_MONEY_WALLETS if w.strip()]
    if not wallets:
        print("No SMART_MONEY_WALLETS configured. Run `--discover` first.")
        return []
    smart_set = set(wallets)

    latest = smartmoney._hex_to_int(discover._rpc("eth_blockNumber", []))
    if not latest:
        print("Could not read latest block from RPC.")
        return []
    from_block = max(0, latest - cfg["lookback_blocks"])
    print(f"[1/3] Scanning incoming transfers to {len(wallets)} smart wallets "
          f"(blocks {from_block}-{latest}) ...")
    events = fetch_incoming_transfers(wallets, from_block, latest)

    # Group by token: distinct smart-wallet recipients, excluding internal
    # shuffles (sender is also a smart wallet) and known base/quote tokens.
    per_token: Dict[str, Set[str]] = defaultdict(set)
    for e in events:
        token = e["token"]
        if token in config.MONITOR_EXCLUDE_TOKENS or not token:
            continue
        if e["from"] in smart_set:        # internal transfer between tracked wallets
            continue
        if e["to"] in smart_set:
            per_token[token].add(e["to"])

    consensus = {t: ws for t, ws in per_token.items() if len(ws) >= cfg["min_consensus"]}
    # Bound the number we validate (each costs GeckoTerminal + honeypot calls).
    ranked = sorted(consensus.items(), key=lambda kv: len(kv[1]), reverse=True)
    ranked = ranked[: cfg["max_validate"]]
    print(f"[2/3] {len(per_token)} tokens received; {len(consensus)} hit consensus "
          f"(>= {cfg['min_consensus']} wallets); validating top {len(ranked)}")

    state = _load_state()
    now = time.time()
    fired: List[Dict[str, Any]] = []
    print("[3/3] Validating consensus tokens (liquidity + honeypot) ...")
    for token, ws in ranked:
        if now - state["alerts"].get(token, 0) < cfg["alert_cooldown_sec"]:
            print(f"    - {token[:10]}.. : on cooldown, skipped")
            continue
        info = token_info(token)
        if not info:
            continue
        if info["liq"] < cfg["min_token_liquidity_usd"] or info["vol24"] < cfg["min_token_volume_24h_usd"]:
            print(f"    - ${info['symbol']:<8}: illiquid/low-vol -> likely spam, skipped")
            continue
        hp = safety.check_honeypot(token) if cfg["check_honeypot"] else {}
        if hp.get("is_honeypot"):
            print(f"    - ${info['symbol']:<8}: HONEYPOT, skipped")
            continue

        msg = _format_alert(token, ws, info, hp)
        print(f"\n    \U0001F525 ALERT: ${info['symbol']} - {len(ws)} smart wallets "
              f"(liq ${info['liq']:,.0f})")
        if send:
            ok, detail = alerts.send_telegram(msg)
            status = "sent \u2705" if ok else f"not sent ({detail})"
            print(f"      telegram: {status}")
        state["alerts"][token] = now
        fired.append({"token": token, "symbol": info["symbol"], "wallets": len(ws),
                      "liq": info["liq"]})
        time.sleep(1.0)

    state["last_block"] = latest
    _save_state(state)
    if not fired:
        print("\nNo new consensus alerts this run.")
    print()
    return fired


if __name__ == "__main__":
    run_monitor()
