from __future__ import annotations

import argparse
import json
import time
from collections import deque
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from binance_client import BinanceTestnet, avg_fill_price
from claude_advisor import ClaudeAdvisor, Decision
from config import Settings, load_settings
from memory import load_recent_decisions
from news_client import NewsClient, infer_currency_code
from notifier import (
    TelegramNotifier,
    format_error,
    format_forced_close,
    format_startup,
    format_trade,
)
from positions import PositionLedger, check_risk


LOG_DIR = Path(__file__).parent / "logs"
STATE_DIR = Path(__file__).parent / "state"
DECISIONS_LOG = LOG_DIR / "decisions.jsonl"
TRADES_LOG = LOG_DIR / "trades.jsonl"
POSITIONS_FILE = STATE_DIR / "positions.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


class RateLimiter:
    def __init__(self, max_per_hour: int) -> None:
        self.max_per_hour = max_per_hour
        self.recent: deque[float] = deque()

    def allow(self) -> bool:
        now = time.time()
        cutoff = now - 3600
        while self.recent and self.recent[0] < cutoff:
            self.recent.popleft()
        return len(self.recent) < self.max_per_hour

    def record(self) -> None:
        self.recent.append(time.time())


def run_once(
    symbol: str,
    settings: Settings,
    binance: BinanceTestnet,
    advisor: ClaudeAdvisor,
    limiter: RateLimiter,
    ledger: PositionLedger,
    dry_run: bool,
    news_client: NewsClient | None = None,
    notifier: TelegramNotifier | None = None,
) -> None:
    print(f"[{_now_iso()}] {symbol}: fetching snapshot")
    snapshot = binance.get_snapshot(symbol)
    print(
        f"  price={snapshot.price} "
        f"{snapshot.base_asset}={snapshot.base_balance} "
        f"{snapshot.quote_asset}={snapshot.quote_balance}"
    )

    ledger.update_peak(symbol, snapshot.price)
    pos = ledger.get(symbol)
    if pos.is_open():
        print(
            f"  open position: qty={pos.base_qty:.6f} entry={pos.avg_entry:.2f} "
            f"peak={pos.peak_since_entry:.2f} pnl={pos.pnl_pct(snapshot.price):+.2f}%"
        )
        risk = check_risk(
            pos,
            snapshot.price,
            settings.stop_loss_pct,
            settings.take_profit_pct,
            settings.trailing_stop_pct,
        )
        if risk.should_close:
            print(f"  RISK EXIT: {risk.reason}")
            if not dry_run:
                _close_position(binance, ledger, symbol, pos.base_qty, snapshot.price, risk.reason)
                if notifier is not None:
                    notifier.send(
                        format_forced_close(symbol, snapshot.price, pos.pnl_pct(snapshot.price), risk.reason)
                    )
            else:
                print("  dry-run: skipping forced close")
            return

    news: list[dict] | None = None
    if news_client is not None:
        try:
            currency = infer_currency_code(symbol)
            news = news_client.fetch(currency, limit=8)
            print(f"  fetched {len(news)} news headlines for {currency}")
        except Exception as exc:
            print(f"  news fetch failed (continuing without): {exc!r}")

    memory = load_recent_decisions(
        DECISIONS_LOG, snapshot.price, settings.memory_depth, symbol=symbol
    )
    if memory:
        print(f"  loaded {len(memory)} past decisions into memory")

    print("  asking Claude…")
    decision = advisor.decide(
        snapshot, settings.max_position_usdt, news=news, memory=memory
    )
    print(
        f"  decision: {decision.action} "
        f"size={decision.size_usdt} conf={decision.confidence:.2f} "
        f"— {decision.reason}"
    )

    _append_jsonl(
        DECISIONS_LOG,
        {
            "ts": _now_iso(),
            "symbol": symbol,
            "price": snapshot.price,
            "balances": {
                snapshot.base_asset: snapshot.base_balance,
                snapshot.quote_asset: snapshot.quote_balance,
            },
            "decision": asdict(decision),
            "dry_run": dry_run,
        },
    )

    should_trade, block_reason = _should_trade(decision, snapshot, settings, limiter)
    if not should_trade:
        print(f"  skip: {block_reason}")
        return

    if dry_run:
        print("  dry-run: would execute but skipping")
        return

    order = _execute(binance, symbol, decision, ledger, snapshot.price)
    limiter.record()
    print(f"  order executed: id={order.get('orderId')} status={order.get('status')}")
    _append_jsonl(
        TRADES_LOG,
        {
            "ts": _now_iso(),
            "symbol": symbol,
            "action": decision.action,
            "requested_usdt": decision.size_usdt,
            "confidence": decision.confidence,
            "reason": decision.reason,
            "order": order,
        },
    )
    if notifier is not None:
        notifier.send(
            format_trade(
                symbol,
                decision.action,
                decision.size_usdt,
                snapshot.price,
                decision.confidence,
                decision.reason,
            )
        )


def _should_trade(
    decision: Decision,
    snapshot,
    settings: Settings,
    limiter: RateLimiter,
) -> tuple[bool, str]:
    if decision.action == "HOLD":
        return False, "HOLD"
    if decision.confidence < settings.min_confidence:
        return False, f"confidence {decision.confidence:.2f} < {settings.min_confidence}"
    if decision.size_usdt <= 0:
        return False, "size_usdt <= 0"
    if decision.size_usdt > settings.max_position_usdt:
        return False, f"size {decision.size_usdt} > cap {settings.max_position_usdt}"
    if not limiter.allow():
        return False, f"rate limit ({settings.max_trades_per_hour}/h) reached"
    if decision.action == "BUY" and snapshot.quote_balance < decision.size_usdt:
        return False, f"insufficient {snapshot.quote_asset} balance"
    if decision.action == "SELL":
        base_needed = decision.size_usdt / snapshot.price
        if snapshot.base_balance < base_needed:
            return False, f"insufficient {snapshot.base_asset} balance"
    return True, ""


def _execute(
    binance: BinanceTestnet,
    symbol: str,
    decision: Decision,
    ledger: PositionLedger,
    reference_price: float,
) -> dict:
    if decision.action == "BUY":
        order = binance.market_buy_quote(symbol, decision.size_usdt)
        qty = _filled_base_qty(order, decision.size_usdt / reference_price)
        fill_price = avg_fill_price(order, reference_price)
        ledger.record_buy(symbol, qty, fill_price)
        return order
    if decision.action == "SELL":
        price = binance.get_price(symbol)
        base_amount = decision.size_usdt / price
        order = binance.market_sell_base(symbol, base_amount)
        qty = _filled_base_qty(order, base_amount)
        ledger.record_sell(symbol, qty)
        return order
    raise RuntimeError(f"unexpected action {decision.action}")


def _close_position(
    binance: BinanceTestnet,
    ledger: PositionLedger,
    symbol: str,
    base_qty: float,
    reference_price: float,
    reason: str,
) -> None:
    # market_sell_base already clips to actual free balance; if the ledger
    # drifted past the exchange balance (fees, precision), we still exit
    # everything the exchange lets us sell.
    order = binance.market_sell_base(symbol, base_qty)
    filled = _filled_base_qty(order, base_qty)
    fill_price = avg_fill_price(order, reference_price)
    ledger.record_sell(symbol, filled)
    _append_jsonl(
        TRADES_LOG,
        {
            "ts": _now_iso(),
            "symbol": symbol,
            "action": "SELL",
            "requested_usdt": filled * fill_price,
            "confidence": 1.0,
            "reason": f"forced_close: {reason}",
            "order": order,
        },
    )


def _filled_base_qty(order: dict, fallback_qty: float) -> float:
    try:
        return float(order.get("executedQty") or fallback_qty)
    except (TypeError, ValueError):
        return fallback_qty


def main() -> None:
    parser = argparse.ArgumentParser(description="Claude-driven Binance testnet bot")
    parser.add_argument("mode", choices=["once", "loop"], help="run one iteration or a continuous loop")
    parser.add_argument("--dry-run", action="store_true", help="override DRY_RUN env to true")
    args = parser.parse_args()

    settings = load_settings()
    dry_run = args.dry_run or settings.dry_run
    print(
        f"config: symbols={','.join(settings.symbols)} interval={settings.interval_minutes}m "
        f"max_pos=${settings.max_position_usdt} min_conf={settings.min_confidence} "
        f"rate={settings.max_trades_per_hour}/h dry_run={dry_run} "
        f"sl={settings.stop_loss_pct}% tp={settings.take_profit_pct}% "
        f"trail={settings.trailing_stop_pct}%"
    )

    binance = BinanceTestnet(settings.binance_api_key, settings.binance_api_secret)
    advisor = ClaudeAdvisor(settings.anthropic_api_key, settings.claude_model)
    limiter = RateLimiter(settings.max_trades_per_hour)
    ledger = PositionLedger(POSITIONS_FILE)
    news_client = NewsClient() if settings.news_enabled else None
    notifier: TelegramNotifier | None = None
    if settings.telegram_bot_token and settings.telegram_chat_id:
        notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
        notifier.send(
            format_startup(
                settings.symbols,
                dry_run,
                settings.stop_loss_pct,
                settings.take_profit_pct,
                settings.trailing_stop_pct,
            ),
            silent=True,
        )

    def _cycle_symbols() -> None:
        for symbol in settings.symbols:
            try:
                run_once(
                    symbol, settings, binance, advisor, limiter, ledger, dry_run,
                    news_client, notifier,
                )
            except Exception as exc:
                print(f"[{_now_iso()}] {symbol}: iteration failed: {exc!r}")
                if notifier is not None:
                    notifier.send(format_error(symbol, exc), silent=True)

    if args.mode == "once":
        _cycle_symbols()
        return

    while True:
        _cycle_symbols()
        sleep_s = settings.interval_minutes * 60
        print(f"sleeping {sleep_s}s")
        time.sleep(sleep_s)


if __name__ == "__main__":
    main()
