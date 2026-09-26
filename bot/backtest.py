"""Bar-by-bar backtester that replays the live bot's rules on historical data.

Assumptions (kept deliberately conservative):
- Prices are BID prices (as MT5 stores them); buys pay the spread on entry,
  sells pay it on exit.
- A signal on the close of bar i is executed at the OPEN of bar i+1, like the
  live bot which trades right after a bar closes.
- If a bar touches both SL and TP, the stop loss is assumed to be hit first.
- If price gaps past SL/TP, the fill is at the bar's open.
- Weekend close: positions are closed at the close of the bar that spans the
  Friday close hour. Loss limits are checked at each bar's open, so a limit
  crossed inside a bar is acted on one bar late.
- Bar times are taken as broker server time (as MT5 exports them).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .hours import TradingHours
from .risk import DailyLossGuard, RiskParams, SymbolSpec, TotalLossGuard, lot_size
from .strategy import Signal, StrategyParams, add_indicators, evaluate, stop_levels


@dataclass(frozen=True)
class BacktestSettings:
    initial_balance: float = 10_000.0
    spread_points: float | None = None     # None -> use the data's 'spread' column
    commission_per_lot: float = 0.0        # round-trip, in account currency
    close_on_opposite_signal: bool = True
    hours: TradingHours = TradingHours()


@dataclass
class Trade:
    side: Signal
    volume: float
    entry_time: pd.Timestamp
    entry_price: float
    sl: float
    tp: float
    exit_time: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str = ""
    profit: float = 0.0


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity: pd.Series
    initial_balance: float
    stats: dict = field(default_factory=dict)

    def trades_frame(self) -> pd.DataFrame:
        return pd.DataFrame([{**t.__dict__, "side": t.side.value} for t in self.trades])


def _pnl(trade: Trade, exit_price: float, spec: SymbolSpec) -> float:
    direction = 1.0 if trade.side is Signal.BUY else -1.0
    return direction * (exit_price - trade.entry_price) / spec.tick_size * spec.tick_value * trade.volume


def run_backtest(
    bars: pd.DataFrame,
    strategy: StrategyParams,
    risk: RiskParams,
    spec: SymbolSpec,
    settings: BacktestSettings = BacktestSettings(),
) -> BacktestResult:
    bars = bars.reset_index(drop=True)
    d = add_indicators(bars, strategy)
    rows = d.to_dict("records")
    times = d["time"].to_numpy()
    o, h, l, c = (d[col].to_numpy(dtype=float) for col in ("open", "high", "low", "close"))
    if settings.spread_points is not None or "spread" not in d:
        spread = np.full(len(d), float(settings.spread_points or 0.0)) * spec.point
    else:
        spread = d["spread"].to_numpy(dtype=float) * spec.point

    balance = settings.initial_balance
    guard = DailyLossGuard(risk.max_daily_loss_pct)
    total_guard = TotalLossGuard(risk.max_total_loss_pct, risk.account_start_balance or settings.initial_balance)
    hours = settings.hours
    halted = False
    diffs = d["time"].diff().dropna()
    bar_span = diffs.median() if len(diffs) else pd.Timedelta(hours=1)
    trades: list[Trade] = []
    position: Trade | None = None
    pending: tuple[Signal, float] | None = None  # (signal, atr) to act on at next open
    equity_curve = np.full(len(d), np.nan)

    def close_position(k: int, price: float, reason: str) -> None:
        nonlocal position, balance
        position.exit_time = pd.Timestamp(times[k])
        position.exit_price = price
        position.exit_reason = reason
        position.profit = _pnl(position, price, spec) - settings.commission_per_lot * position.volume
        balance += position.profit
        trades.append(position)
        position = None

    def exit_price_at(price_bid: float, k: int) -> float:
        # Closing a buy sells at bid; closing a sell buys at ask.
        return price_bid if position.side is Signal.BUY else price_bid + spread[k]

    start = max(strategy.min_bars - 1, 1)
    for k in range(start, len(d)):
        ts = pd.Timestamp(times[k])
        open_equity = balance + (_pnl(position, exit_price_at(o[k], k), spec) if position else 0.0)
        guard.update(ts.date(), open_equity)

        # 0) Loss limits, checked at the open.
        if not halted and total_guard.breached(open_equity):
            halted = True
            if position is not None:
                close_position(k, exit_price_at(o[k], k), "total loss limit")
        daily_ok = guard.trading_allowed(open_equity)
        if not daily_ok and position is not None and risk.close_on_limit:
            close_position(k, exit_price_at(o[k], k), "daily loss limit")

        # 1) Act on the previous bar's signal at this bar's open.
        if pending is not None:
            sig, sig_atr = pending
            pending = None
            if position is not None and position.side is not sig and settings.close_on_opposite_signal:
                close_position(k, exit_price_at(o[k], k), "opposite signal")
            if position is None and not halted and daily_ok and hours.entries_allowed(ts):
                if spread[k] / spec.point <= risk.max_spread_points:
                    entry = o[k] + spread[k] if sig is Signal.BUY else o[k]
                    sl, tp = stop_levels(sig, entry, sig_atr, strategy)
                    volume = lot_size(balance, risk.risk_per_trade_pct, abs(entry - sl), spec)
                    if volume > 0:
                        position = Trade(sig, volume, ts, entry, sl, tp)

        # 2) Stop loss / take profit inside this bar.
        if position is not None:
            p = position
            if p.side is Signal.BUY:
                lo, hi, op = l[k], h[k], o[k]
                if op <= p.sl:
                    close_position(k, op, "stop loss (gap)")
                elif op >= p.tp:
                    close_position(k, op, "take profit (gap)")
                elif lo <= p.sl:
                    close_position(k, p.sl, "stop loss")
                elif hi >= p.tp:
                    close_position(k, p.tp, "take profit")
            else:
                s = spread[k]
                lo, hi, op = l[k] + s, h[k] + s, o[k] + s  # ask prices
                if op >= p.sl:
                    close_position(k, op, "stop loss (gap)")
                elif op <= p.tp:
                    close_position(k, op, "take profit (gap)")
                elif hi >= p.sl:
                    close_position(k, p.sl, "stop loss")
                elif lo <= p.tp:
                    close_position(k, p.tp, "take profit")

        # 2b) Weekend close at the end of the bar that spans the Friday close hour.
        if position is not None and hours.weekend_close_due(ts + bar_span - pd.Timedelta(seconds=1)):
            close_position(k, exit_price_at(c[k], k), "weekend close")

        # 3) Signal on this bar's close, executed at the next open.
        if k + 1 < len(d):
            res = evaluate(rows[k - 1], rows[k], strategy)
            if res.signal is not Signal.NONE and not (position and position.side is res.signal):
                pending = (res.signal, res.atr)

        equity_curve[k] = balance + (_pnl(position, exit_price_at(c[k], k), spec) if position else 0.0)

    if position is not None:
        close_position(len(d) - 1, exit_price_at(c[-1], len(d) - 1), "end of data")
        equity_curve[-1] = balance

    equity = pd.Series(equity_curve, index=pd.to_datetime(times)).dropna()
    result = BacktestResult(trades, equity, settings.initial_balance)
    result.stats = compute_stats(result)
    return result


def compute_stats(r: BacktestResult) -> dict:
    profits = np.array([t.profit for t in r.trades], dtype=float)
    wins, losses = profits[profits > 0], profits[profits <= 0]
    final = r.initial_balance + profits.sum()
    eq = r.equity if not r.equity.empty else pd.Series([r.initial_balance])
    peak = eq.cummax()
    max_dd_pct = float(((peak - eq) / peak).max() * 100) if len(eq) else 0.0
    gross_loss = -losses.sum()
    return {
        "period": f"{eq.index[0]} -> {eq.index[-1]}" if isinstance(eq.index, pd.DatetimeIndex) else "",
        "trades": int(len(profits)),
        "win_rate_pct": float(len(wins) / len(profits) * 100) if len(profits) else 0.0,
        "net_profit": float(profits.sum()),
        "return_pct": float((final / r.initial_balance - 1) * 100),
        "final_balance": float(final),
        "max_drawdown_pct": max_dd_pct,
        "profit_factor": float(wins.sum() / gross_loss) if gross_loss > 0 else float("inf") if len(wins) else 0.0,
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "largest_loss": float(losses.min()) if len(losses) else 0.0,
    }


def format_report(stats: dict, title: str = "") -> str:
    lines = [f"===== Backtest {title} =====".strip()]
    labels = [
        ("period", "Period", "{}"),
        ("trades", "Antal affärer", "{}"),
        ("win_rate_pct", "Vinstandel", "{:.1f} %"),
        ("net_profit", "Nettoresultat", "{:,.2f}"),
        ("return_pct", "Avkastning", "{:+.2f} %"),
        ("final_balance", "Slutsaldo", "{:,.2f}"),
        ("max_drawdown_pct", "Max drawdown", "{:.2f} %"),
        ("profit_factor", "Profit factor", "{:.2f}"),
        ("avg_win", "Snittvinst", "{:,.2f}"),
        ("avg_loss", "Snittförlust", "{:,.2f}"),
        ("largest_loss", "Största förlust", "{:,.2f}"),
    ]
    for key, label, fmt in labels:
        lines.append(f"{label:<17}{fmt.format(stats[key])}")
    return "\n".join(lines)
