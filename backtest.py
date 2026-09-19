"""Backtest the Claude-driven strategy on historical Binance candles.

We fetch a long span of 1h/15m/5m candles from the PUBLIC Binance endpoint
(no keys needed for read-only market data) and step through 5m bars. At each
step we build the same MarketSnapshot the live bot would see, ask Claude for
a decision, and update a simulated account. At the end we print PnL,
drawdown, win rate, number of trades.

Cost warning: each iteration is one Claude call. With --steps 100 and
sonnet-5 you'll pay a few cents to a dollar. Use --dry-strategy to run a
simple RSI baseline instead of Claude if you just want to sanity-check the
harness.

Usage:
    python backtest.py --symbol BTCUSDT --days 14 --steps 200
    python backtest.py --symbol ETHUSDT --days 30 --steps 200 --dry-strategy
"""

from __future__ import annotations

import argparse
import statistics
from dataclasses import dataclass, field

from binance.client import Client
from binance.enums import KLINE_INTERVAL_5MINUTE, KLINE_INTERVAL_15MINUTE, KLINE_INTERVAL_1HOUR

import indicators
from binance_client import Candle, MarketSnapshot
from claude_advisor import ClaudeAdvisor, Decision
from config import load_settings


TF_INTERVAL = {
    "1h": (KLINE_INTERVAL_1HOUR, 60 * 60 * 1000),
    "15m": (KLINE_INTERVAL_15MINUTE, 15 * 60 * 1000),
    "5m": (KLINE_INTERVAL_5MINUTE, 5 * 60 * 1000),
}


@dataclass
class SimAccount:
    quote_balance: float
    base_balance: float = 0.0
    trades: list[dict] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)

    def equity(self, price: float) -> float:
        return self.quote_balance + self.base_balance * price

    def buy(self, usdt: float, price: float, ts_ms: int) -> None:
        if usdt <= 0 or usdt > self.quote_balance:
            return
        qty = usdt / price
        self.quote_balance -= usdt
        self.base_balance += qty
        self.trades.append({"ts": ts_ms, "side": "BUY", "usdt": usdt, "price": price})

    def sell(self, usdt_notional: float, price: float, ts_ms: int) -> None:
        qty = min(self.base_balance, usdt_notional / price)
        if qty <= 0:
            return
        self.quote_balance += qty * price
        self.base_balance -= qty
        self.trades.append({"ts": ts_ms, "side": "SELL", "usdt": qty * price, "price": price})


def fetch_history(client: Client, symbol: str, days: int) -> dict[str, list[Candle]]:
    """Fetch enough history to have 250+ candles ahead of the first backtest step."""
    limit = 1000
    result: dict[str, list[Candle]] = {}
    for tf, (interval, _ms) in TF_INTERVAL.items():
        # get_historical_klines fetches everything within the window
        raw = client.get_historical_klines(symbol, interval, f"{days + 3} days ago UTC")
        result[tf] = [
            Candle(
                open_time_ms=int(k[0]),
                open=float(k[1]),
                high=float(k[2]),
                low=float(k[3]),
                close=float(k[4]),
                volume=float(k[5]),
            )
            for k in raw
        ]
    print(f"  fetched: " + ", ".join(f"{tf}={len(v)}" for tf, v in result.items()))
    return result


def slice_up_to(candles: list[Candle], ts_ms: int, max_candles: int = 250) -> list[Candle]:
    """All candles whose open_time <= ts_ms, last `max_candles` of them."""
    cut = [c for c in candles if c.open_time_ms <= ts_ms]
    return cut[-max_candles:]


def build_snapshot(
    symbol: str, price: float, history: dict[str, list[Candle]], ts_ms: int, account: SimAccount
) -> MarketSnapshot:
    tf_slices = {tf: slice_up_to(candles, ts_ms) for tf, candles in history.items()}
    return MarketSnapshot(
        symbol=symbol,
        price=price,
        candles_by_tf=tf_slices,
        base_asset=symbol.replace("USDT", ""),
        quote_asset="USDT",
        base_balance=account.base_balance,
        quote_balance=account.quote_balance,
    )


def rsi_baseline(snapshot: MarketSnapshot) -> Decision:
    """Cheap non-LLM strategy for comparing against Claude: RSI mean-reversion on 15m."""
    snap = indicators.compute(snapshot.candles_by_tf["15m"], "15m")
    if snap.rsi_14 < 30 and snapshot.quote_balance > 20:
        size = min(snapshot.quote_balance * 0.5, 200)
        return Decision("BUY", size, 0.8, f"RSI15m={snap.rsi_14:.1f} <30", {"strategy": "rsi"})
    if snap.rsi_14 > 70 and snapshot.base_balance * snapshot.price > 20:
        size = min(snapshot.base_balance * snapshot.price * 0.5, 200)
        return Decision("SELL", size, 0.8, f"RSI15m={snap.rsi_14:.1f} >70", {"strategy": "rsi"})
    return Decision("HOLD", 0.0, 0.4, f"RSI15m={snap.rsi_14:.1f} mid", {"strategy": "rsi"})


def run_backtest(
    symbol: str,
    days: int,
    steps: int,
    starting_usdt: float,
    max_position_usdt: float,
    min_confidence: float,
    use_claude: bool,
) -> None:
    settings = load_settings()

    print(f"loading history for {symbol} ({days} days)…")
    client = Client()  # public endpoints, no keys required
    history = fetch_history(client, symbol, days)

    fivem = history["5m"]
    if len(fivem) < 250 + steps:
        raise SystemExit(f"not enough 5m candles: have {len(fivem)}, need {250 + steps}")

    advisor = ClaudeAdvisor(settings.anthropic_api_key, settings.claude_model) if use_claude else None
    account = SimAccount(quote_balance=starting_usdt)
    peak_equity = starting_usdt
    max_dd_pct = 0.0
    step_stride = max(1, (len(fivem) - 250) // steps)

    print(
        f"backtest: symbol={symbol} steps={steps} stride={step_stride} "
        f"strategy={'claude' if use_claude else 'rsi-baseline'}"
    )

    step_minutes = 5 * step_stride
    steps_per_year = (365 * 24 * 60) / step_minutes if step_minutes > 0 else 252

    for i in range(250, len(fivem), step_stride):
        candle = fivem[i]
        price = candle.close
        ts = candle.open_time_ms
        snapshot = build_snapshot(symbol, price, history, ts, account)

        try:
            if advisor is not None:
                decision = advisor.decide(snapshot, max_position_usdt)
            else:
                decision = rsi_baseline(snapshot)
        except Exception as exc:
            print(f"  step {i}: decision failed: {exc!r}")
            continue

        traded = False
        if decision.action != "HOLD" and decision.confidence >= min_confidence:
            size = min(decision.size_usdt, max_position_usdt)
            if decision.action == "BUY" and size > 0 and size <= account.quote_balance:
                account.buy(size, price, ts)
                traded = True
            elif decision.action == "SELL" and size > 0 and account.base_balance * price >= size:
                account.sell(size, price, ts)
                traded = True

        equity = account.equity(price)
        account.equity_curve.append(equity)
        peak_equity = max(peak_equity, equity)
        dd_pct = ((peak_equity - equity) / peak_equity) * 100
        max_dd_pct = max(max_dd_pct, dd_pct)

        marker = "*" if traded else " "
        print(
            f"  step {i:5d} ts={ts} px={price:.2f} eq={equity:9.2f} "
            f"dd={dd_pct:5.2f}% {marker}{decision.action:4s} "
            f"conf={decision.confidence:.2f} — {decision.reason[:80]}"
        )

        if len(account.trades) >= steps:
            break

    _print_summary(account, starting_usdt, max_dd_pct, fivem[-1].close, steps_per_year)


def _print_summary(
    account: SimAccount,
    starting_usdt: float,
    max_dd_pct: float,
    final_price: float,
    steps_per_year: float,
) -> None:
    final_eq = account.equity(final_price)
    pnl_pct = (final_eq - starting_usdt) / starting_usdt * 100
    n_trades = len(account.trades)

    print("\n=== backtest summary ===")
    print(f"starting equity : ${starting_usdt:.2f}")
    print(f"final equity    : ${final_eq:.2f}")
    print(f"PnL             : {pnl_pct:+.2f}%")
    print(f"trades          : {n_trades}")
    print(f"max drawdown    : {max_dd_pct:.2f}%")

    if len(account.equity_curve) > 1:
        returns = [
            (account.equity_curve[i] / account.equity_curve[i - 1]) - 1
            for i in range(1, len(account.equity_curve))
        ]
        if returns and statistics.stdev(returns) > 0:
            sharpe = (statistics.mean(returns) / statistics.stdev(returns)) * (steps_per_year ** 0.5)
            print(f"sharpe (naive)  : {sharpe:.2f} (annualized at {steps_per_year:.0f} steps/yr)")

    if n_trades:
        buys = [t for t in account.trades if t["side"] == "BUY"]
        sells = [t for t in account.trades if t["side"] == "SELL"]
        avg_buy = sum(t["price"] for t in buys) / len(buys) if buys else 0
        avg_sell = sum(t["price"] for t in sells) / len(sells) if sells else 0
        print(f"avg buy price   : {avg_buy:.2f}")
        print(f"avg sell price  : {avg_sell:.2f}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--days", type=int, default=14, help="days of history to load")
    p.add_argument("--steps", type=int, default=100, help="number of decision steps to run")
    p.add_argument("--starting-usdt", type=float, default=10000.0)
    p.add_argument("--max-position-usdt", type=float, default=500.0)
    p.add_argument("--min-confidence", type=float, default=0.7)
    p.add_argument(
        "--dry-strategy",
        action="store_true",
        help="use a simple RSI baseline instead of Claude (free, fast)",
    )
    args = p.parse_args()

    run_backtest(
        symbol=args.symbol,
        days=args.days,
        steps=args.steps,
        starting_usdt=args.starting_usdt,
        max_position_usdt=args.max_position_usdt,
        min_confidence=args.min_confidence,
        use_claude=not args.dry_strategy,
    )


if __name__ == "__main__":
    main()
