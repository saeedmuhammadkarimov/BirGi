from __future__ import annotations

import argparse
import json
import time
from collections import deque
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from binance_client import BinanceTestnet
from claude_advisor import ClaudeAdvisor, Decision
from config import Settings, load_settings
from news_client import NewsClient, infer_currency_code


LOG_DIR = Path(__file__).parent / "logs"
DECISIONS_LOG = LOG_DIR / "decisions.jsonl"
TRADES_LOG = LOG_DIR / "trades.jsonl"


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
    settings: Settings,
    binance: BinanceTestnet,
    advisor: ClaudeAdvisor,
    limiter: RateLimiter,
    dry_run: bool,
    news_client: NewsClient | None = None,
) -> None:
    print(f"[{_now_iso()}] fetching snapshot for {settings.symbol}")
    snapshot = binance.get_snapshot(settings.symbol)
    print(
        f"  price={snapshot.price} "
        f"{snapshot.base_asset}={snapshot.base_balance} "
        f"{snapshot.quote_asset}={snapshot.quote_balance}"
    )

    news: list[dict] | None = None
    if news_client is not None:
        try:
            currency = infer_currency_code(settings.symbol)
            news = news_client.fetch(currency, limit=8)
            print(f"  fetched {len(news)} news headlines for {currency}")
        except Exception as exc:
            print(f"  news fetch failed (continuing without): {exc!r}")

    print("  asking Claude…")
    decision = advisor.decide(snapshot, settings.max_position_usdt, news=news)
    print(
        f"  decision: {decision.action} "
        f"size={decision.size_usdt} conf={decision.confidence:.2f} "
        f"— {decision.reason}"
    )

    _append_jsonl(
        DECISIONS_LOG,
        {
            "ts": _now_iso(),
            "symbol": settings.symbol,
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

    order = _execute(binance, snapshot.symbol, decision)
    limiter.record()
    print(f"  order executed: id={order.get('orderId')} status={order.get('status')}")
    _append_jsonl(
        TRADES_LOG,
        {
            "ts": _now_iso(),
            "symbol": settings.symbol,
            "action": decision.action,
            "requested_usdt": decision.size_usdt,
            "confidence": decision.confidence,
            "reason": decision.reason,
            "order": order,
        },
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


def _execute(binance: BinanceTestnet, symbol: str, decision: Decision) -> dict:
    if decision.action == "BUY":
        return binance.market_buy_quote(symbol, decision.size_usdt)
    if decision.action == "SELL":
        price = binance.get_price(symbol)
        base_amount = decision.size_usdt / price
        return binance.market_sell_base(symbol, base_amount)
    raise RuntimeError(f"unexpected action {decision.action}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Claude-driven Binance testnet bot")
    parser.add_argument("mode", choices=["once", "loop"], help="run one iteration or a continuous loop")
    parser.add_argument("--dry-run", action="store_true", help="override DRY_RUN env to true")
    args = parser.parse_args()

    settings = load_settings()
    dry_run = args.dry_run or settings.dry_run
    print(
        f"config: symbol={settings.symbol} interval={settings.interval_minutes}m "
        f"max_pos=${settings.max_position_usdt} min_conf={settings.min_confidence} "
        f"rate={settings.max_trades_per_hour}/h dry_run={dry_run}"
    )

    binance = BinanceTestnet(settings.binance_api_key, settings.binance_api_secret)
    advisor = ClaudeAdvisor(settings.anthropic_api_key, settings.claude_model)
    limiter = RateLimiter(settings.max_trades_per_hour)
    news_client = NewsClient(settings.cryptopanic_token) if settings.cryptopanic_token else None

    if args.mode == "once":
        run_once(settings, binance, advisor, limiter, dry_run, news_client)
        return

    while True:
        try:
            run_once(settings, binance, advisor, limiter, dry_run, news_client)
        except Exception as exc:
            print(f"[{_now_iso()}] iteration failed: {exc!r}")
        sleep_s = settings.interval_minutes * 60
        print(f"sleeping {sleep_s}s")
        time.sleep(sleep_s)


if __name__ == "__main__":
    main()
