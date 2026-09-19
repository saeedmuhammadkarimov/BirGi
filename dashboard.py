"""One-shot CLI dashboard — reads logs and state, prints the current picture.

    python dashboard.py           # everything
    python dashboard.py --tail 20 # last 20 decisions only

Purely local — reads files in logs/ and state/, no network, no keys, no Claude
call. Safe to run anytime, including while `bot.py loop` is running.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).parent
LOG_DIR = ROOT / "logs"
STATE_DIR = ROOT / "state"
DECISIONS_LOG = LOG_DIR / "decisions.jsonl"
TRADES_LOG = LOG_DIR / "trades.jsonl"
POSITIONS_FILE = STATE_DIR / "positions.json"


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _read_positions() -> dict:
    if not POSITIONS_FILE.exists():
        return {}
    try:
        return json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _fmt_ts(ts: str) -> str:
    if not ts:
        return "?"
    try:
        return datetime.fromisoformat(ts).astimezone().strftime("%m-%d %H:%M")
    except (TypeError, ValueError):
        return ts[:16]


def _hr(char: str = "─", width: int = 78) -> str:
    return char * width


def print_open_positions(positions: dict) -> None:
    print(_hr("═"))
    print("OPEN POSITIONS")
    print(_hr())
    open_pos = {s: p for s, p in positions.items() if p.get("base_qty", 0) > 1e-12}
    if not open_pos:
        print("  (none)")
        return
    print(f"  {'symbol':<10} {'qty':>12} {'entry':>10} {'peak':>10} {'ATR':>8}  opened")
    for symbol, p in open_pos.items():
        print(
            f"  {symbol:<10} "
            f"{p['base_qty']:>12.6f} "
            f"{p['avg_entry']:>10.2f} "
            f"{p['peak_since_entry']:>10.2f} "
            f"{p.get('atr_at_entry', 0):>8.2f}  "
            f"{_fmt_ts(p.get('opened_at', ''))}"
        )


def print_recent_decisions(decisions: list[dict], tail: int) -> None:
    print()
    print(_hr("═"))
    print(f"RECENT DECISIONS (last {min(tail, len(decisions))} of {len(decisions)})")
    print(_hr())
    if not decisions:
        print("  (none yet — bot hasn't run)")
        return
    print(f"  {'time':<12} {'symbol':<10} {'action':<5} {'conf':>5} {'price':>10}  reason")
    for d in decisions[-tail:]:
        dec = d.get("decision", {})
        reason = (dec.get("reason") or "")[:60]
        print(
            f"  {_fmt_ts(d.get('ts', '')):<12} "
            f"{d.get('symbol', ''):<10} "
            f"{dec.get('action', ''):<5} "
            f"{dec.get('confidence', 0):>5.2f} "
            f"{d.get('price', 0):>10.2f}  "
            f"{reason}"
        )


def print_recent_trades(trades: list[dict], tail: int) -> None:
    print()
    print(_hr("═"))
    print(f"EXECUTED TRADES (last {min(tail, len(trades))} of {len(trades)})")
    print(_hr())
    if not trades:
        print("  (none yet)")
        return
    print(f"  {'time':<12} {'symbol':<10} {'side':<5} {'usdt':>10}  reason")
    for t in trades[-tail:]:
        reason = (t.get("reason") or "")[:60]
        print(
            f"  {_fmt_ts(t.get('ts', '')):<12} "
            f"{t.get('symbol', ''):<10} "
            f"{t.get('action', ''):<5} "
            f"{t.get('requested_usdt', 0):>10.2f}  "
            f"{reason}"
        )


def print_cost_summary(decisions: list[dict]) -> None:
    print()
    print(_hr("═"))
    print("CLAUDE API COST")
    print(_hr())
    total_in = total_out = total_cache_r = total_cache_w = 0
    total_cost = 0.0
    for d in decisions:
        u = d.get("usage") or {}
        total_in += u.get("input_tokens", 0)
        total_out += u.get("output_tokens", 0)
        total_cache_r += u.get("cache_read_input_tokens", 0)
        total_cache_w += u.get("cache_creation_input_tokens", 0)
        total_cost += float(d.get("cost_usd", 0) or 0)

    print(f"  calls          : {len(decisions)}")
    print(f"  input tokens   : {total_in:,}")
    print(f"  output tokens  : {total_out:,}")
    print(f"  cache reads    : {total_cache_r:,}")
    print(f"  cache writes   : {total_cache_w:,}")
    print(f"  total cost     : ${total_cost:.4f}")
    if decisions:
        print(f"  avg per call   : ${total_cost / len(decisions):.5f}")
        if total_cache_r + total_in > 0:
            hit_rate = total_cache_r / (total_cache_r + total_in) * 100
            print(f"  cache hit rate : {hit_rate:.1f}%")


def print_decision_mix(decisions: list[dict]) -> None:
    print()
    print(_hr("═"))
    print("DECISION MIX")
    print(_hr())
    if not decisions:
        print("  (none)")
        return
    tally: dict[str, dict[str, int]] = {}
    for d in decisions:
        sym = d.get("symbol", "?")
        act = d.get("decision", {}).get("action", "?")
        tally.setdefault(sym, {"BUY": 0, "SELL": 0, "HOLD": 0})
        tally[sym][act] = tally[sym].get(act, 0) + 1
    print(f"  {'symbol':<10} {'BUY':>6} {'SELL':>6} {'HOLD':>6}  total")
    for sym in sorted(tally):
        row = tally[sym]
        total = sum(row.values())
        print(
            f"  {sym:<10} "
            f"{row.get('BUY', 0):>6} "
            f"{row.get('SELL', 0):>6} "
            f"{row.get('HOLD', 0):>6}  "
            f"{total:>5}"
        )


def print_pnl_estimate(positions: dict, trades: list[dict]) -> None:
    """Rough per-symbol realized PnL from executed trades log."""
    print()
    print(_hr("═"))
    print("REALIZED PnL (from trades log; ignores fees, uses request_usdt)")
    print(_hr())
    per_symbol_flow: dict[str, float] = {}
    for t in trades:
        sym = t.get("symbol", "?")
        side = t.get("action", "")
        amt = float(t.get("requested_usdt", 0) or 0)
        if side == "BUY":
            per_symbol_flow[sym] = per_symbol_flow.get(sym, 0) - amt
        elif side == "SELL":
            per_symbol_flow[sym] = per_symbol_flow.get(sym, 0) + amt
    if not per_symbol_flow:
        print("  (no trades yet)")
        return
    print(f"  {'symbol':<10} {'realized':>12}  note")
    for sym, flow in sorted(per_symbol_flow.items()):
        note = ""
        if sym in positions and positions[sym].get("base_qty", 0) > 1e-12:
            note = "(has open position — realized only)"
        print(f"  {sym:<10} {flow:>+12.2f}  {note}")


def main() -> None:
    parser = argparse.ArgumentParser(description="BirGi bot status snapshot")
    parser.add_argument("--tail", type=int, default=10, help="show last N decisions/trades")
    args = parser.parse_args()

    decisions = _read_jsonl(DECISIONS_LOG)
    trades = _read_jsonl(TRADES_LOG)
    positions = _read_positions()

    print()
    print(_hr("═"))
    print(f"BirGi Dashboard — {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    print(_hr("═"))

    print_open_positions(positions)
    print_recent_decisions(decisions, args.tail)
    print_recent_trades(trades, args.tail)
    print_pnl_estimate(positions, trades)
    print_decision_mix(decisions)
    print_cost_summary(decisions)
    print()


if __name__ == "__main__":
    main()
