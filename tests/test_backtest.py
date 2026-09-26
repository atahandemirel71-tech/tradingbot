import numpy as np
import pandas as pd
import pytest

from backtest import load_csv
from bot.backtest import BacktestSettings, run_backtest
from bot.hours import TradingHours
from bot.risk import RiskParams, SymbolSpec
from bot.strategy import Signal, StrategyParams, generate_signal

PARAMS = StrategyParams(fast_ema=3, slow_ema=8, trend_ema=20, rsi_period=5, atr_period=5, use_adx_filter=False,
                        sl_atr_mult=1.5, tp_atr_mult=3.0, trailing_atr_mult=0, breakeven_at_r=0)
SPEC = SymbolSpec(0.00001, 1.0, 0.01, 100.0, 0.01, point=0.00001, digits=5)
RISK = RiskParams(risk_per_trade_pct=1.0, max_daily_loss_pct=0, max_total_loss_pct=0, max_spread_points=50)
NO_SPREAD = BacktestSettings(initial_balance=10_000, spread_points=0)


def bars(close, high=None, low=None):
    close = np.asarray(close, dtype=float)
    return pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=len(close), freq="15min", tz="UTC"),
        "open": np.concatenate([[close[0]], close[:-1]]),
        "high": close + 0.0005 if high is None else high,
        "low": close - 0.0005 if low is None else low,
        "close": close,
    })


def series_until_buy():
    """Closes ending exactly on the bar that produces the first BUY signal."""
    close = list(np.linspace(1.00, 1.10, 80)) + list(np.linspace(1.10, 1.095, 6))
    for extra in np.linspace(1.096, 1.12, 10):
        close.append(extra)
        if generate_signal(bars(close), PARAMS).signal is Signal.BUY:
            return close
    raise AssertionError("fixture never produced a BUY")


def test_buy_enters_next_bar_open_and_hits_take_profit():
    close = series_until_buy()
    signal_bar = len(close) - 1
    close = close + list(np.linspace(close[-1], close[-1] + 0.05, 40))
    r = run_backtest(bars(close), PARAMS, RISK, SPEC, NO_SPREAD)
    first = r.trades[0]
    assert first.side is Signal.BUY
    assert first.entry_time == bars(close)["time"].iloc[signal_bar + 1]  # no look-ahead
    assert first.exit_reason == "take profit"
    # TP is 2x the SL distance, SL risks 1 % -> about +2 % (less due to lot rounding).
    assert 150 < first.profit <= 200


def test_stop_loss_loses_about_one_percent():
    close = series_until_buy()
    close = close + list(np.linspace(close[-1], close[-1] - 0.05, 40))
    r = run_backtest(bars(close), PARAMS, RISK, SPEC, NO_SPREAD)
    first = r.trades[0]
    assert first.exit_reason.startswith("stop loss")
    assert -110 < first.profit < -90


def test_bar_touching_sl_and_tp_counts_as_loss():
    close = series_until_buy()
    close = close + [close[-1]] * 3
    df = bars(close)
    # Huge range on the bar after entry touches both SL and TP.
    df.loc[len(df) - 2, "high"] += 0.05
    df.loc[len(df) - 2, "low"] -= 0.05
    r = run_backtest(df, PARAMS, RISK, SPEC, NO_SPREAD)
    assert r.trades[0].exit_reason == "stop loss"


def test_buy_fills_at_ask():
    close = series_until_buy()
    close = close + list(np.linspace(close[-1], close[-1] + 0.05, 40))
    df = bars(close)
    no_spread = run_backtest(df, PARAMS, RISK, SPEC, NO_SPREAD).trades[0]
    with_spread = run_backtest(df, PARAMS, RISK, SPEC, BacktestSettings(10_000, spread_points=20)).trades[0]
    assert with_spread.entry_price == pytest.approx(no_spread.entry_price + 20 * SPEC.point)


def test_spread_makes_results_worse():
    rng = np.random.default_rng(3)
    df = bars(1.1 + np.cumsum(rng.normal(0, 0.0008, 3000)))
    no_spread = run_backtest(df, PARAMS, RISK, SPEC, NO_SPREAD)
    with_spread = run_backtest(df, PARAMS, RISK, SPEC, BacktestSettings(10_000, spread_points=20))
    assert with_spread.stats["net_profit"] < no_spread.stats["net_profit"]


def test_stats_are_consistent_on_random_walk():
    rng = np.random.default_rng(7)
    close = 1.1 + np.cumsum(rng.normal(0, 0.0008, 3000))
    r = run_backtest(bars(close), PARAMS, RISK, SPEC, BacktestSettings(10_000, spread_points=10))
    s = r.stats
    assert s["trades"] > 10
    assert s["final_balance"] == pytest.approx(10_000 + sum(t.profit for t in r.trades))
    assert r.equity.iloc[-1] == pytest.approx(s["final_balance"])
    assert 0 <= s["max_drawdown_pct"] < 100
    for t in r.trades:
        assert t.exit_time >= t.entry_time


def test_load_csv_mt5_export(tmp_path):
    f = tmp_path / "EURUSD_M15.csv"
    f.write_text(
        "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>\n"
        "2026.01.02\t00:15:00\t1.1001\t1.1010\t1.0990\t1.1005\t100\t0\t12\n"
        "2026.01.02\t00:00:00\t1.1000\t1.1008\t1.0995\t1.1001\t90\t0\t10\n"
    )
    df = load_csv(str(f))
    assert list(df.columns) == ["time", "open", "high", "low", "close", "spread"]
    assert df["time"].is_monotonic_increasing and df["spread"].iloc[0] == 10


def test_load_csv_generic(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("time,open,high,low,close\n2026-01-01 00:00,1,1.1,0.9,1.05\n")
    df = load_csv(str(f))
    assert df["time"].iloc[0] == pd.Timestamp("2026-01-01", tz="UTC")


def random_bars(seed=7, n=3000):
    rng = np.random.default_rng(seed)
    return bars(1.1 + np.cumsum(rng.normal(0, 0.0008, n)))


def test_no_position_held_over_weekend():
    r = run_backtest(random_bars(), PARAMS, RISK, SPEC, BacktestSettings(10_000, spread_points=10))
    assert any(t.exit_reason == "weekend close" for t in r.trades)
    for t in r.trades:
        # Opened and closed in the same trading week, never held through Saturday/Sunday.
        assert t.entry_time.isocalendar()[:2] == t.exit_time.isocalendar()[:2]
        assert t.entry_time.weekday() < 5 and t.exit_time.weekday() < 5
        assert not (t.entry_time.weekday() == 4 and t.entry_time.hour >= 20)


def test_session_filter_limits_entry_hours():
    hours = TradingHours(close_before_weekend=False, use_session_filter=True, session_start_hour=8, session_end_hour=12)
    r = run_backtest(random_bars(), PARAMS, RISK, SPEC, BacktestSettings(10_000, spread_points=10, hours=hours))
    assert r.trades and all(8 <= t.entry_time.hour < 12 for t in r.trades)


def test_total_loss_limit_stops_trading():
    risk = RiskParams(risk_per_trade_pct=1.0, max_daily_loss_pct=0, max_total_loss_pct=3, max_spread_points=50)
    unlimited = run_backtest(random_bars(1), PARAMS, RISK, SPEC, BacktestSettings(10_000, spread_points=20))
    limited = run_backtest(random_bars(1), PARAMS, risk, SPEC, BacktestSettings(10_000, spread_points=20))
    assert unlimited.stats["return_pct"] < -3  # fixture really loses more than the limit
    assert len(limited.trades) < len(unlimited.trades)
    # Stops close to the limit: at most about one trade's risk beyond it.
    assert limited.stats["return_pct"] > -3 - 2.5


def test_adx_filter_reduces_trades():
    with_adx = StrategyParams(fast_ema=3, slow_ema=8, trend_ema=20, rsi_period=5, atr_period=5,
                              sl_atr_mult=1.5, tp_atr_mult=3.0, trailing_atr_mult=0, breakeven_at_r=0,
                              use_adx_filter=True, adx_min=30)
    base = run_backtest(random_bars(), PARAMS, RISK, SPEC, NO_SPREAD)
    filtered = run_backtest(random_bars(), with_adx, RISK, SPEC, NO_SPREAD)
    assert 0 < len(filtered.trades) < len(base.trades)


def test_trailing_stop_lets_winner_run_past_fixed_target():
    close = series_until_buy()
    entry_close = close[-1]
    # Strong trend up, then a sharp reversal.
    close = close + list(np.linspace(entry_close, entry_close + 0.06, 60)) + list(np.linspace(entry_close + 0.06, entry_close, 30))
    trail = StrategyParams(fast_ema=3, slow_ema=8, trend_ema=20, rsi_period=5, atr_period=5, use_adx_filter=False,
                           sl_atr_mult=1.5, tp_atr_mult=0, trailing_atr_mult=3.0, breakeven_at_r=1.0)
    fixed = run_backtest(bars(close), PARAMS, RISK, SPEC, NO_SPREAD).trades[0]
    trailed = run_backtest(bars(close), trail, RISK, SPEC, NO_SPREAD).trades[0]
    assert fixed.exit_reason == "take profit"
    assert trailed.exit_reason in ("trailing stop", "opposite signal")
    assert trailed.profit > fixed.profit * 2


def test_breakeven_turns_loser_into_scratch():
    close = series_until_buy()
    e = close[-1]
    # Goes up about 1.5 R, then collapses far below the original stop.
    close = close + list(np.linspace(e, e + 0.004, 8)) + list(np.linspace(e + 0.004, e - 0.02, 10))
    be = StrategyParams(fast_ema=3, slow_ema=8, trend_ema=20, rsi_period=5, atr_period=5, use_adx_filter=False,
                        sl_atr_mult=1.5, tp_atr_mult=0, trailing_atr_mult=0, breakeven_at_r=1.0)
    no_be = StrategyParams(fast_ema=3, slow_ema=8, trend_ema=20, rsi_period=5, atr_period=5, use_adx_filter=False,
                           sl_atr_mult=1.5, tp_atr_mult=0, trailing_atr_mult=0, breakeven_at_r=0)
    t_be = run_backtest(bars(close), be, RISK, SPEC, NO_SPREAD).trades[0]
    t_no = run_backtest(bars(close), no_be, RISK, SPEC, NO_SPREAD).trades[0]
    assert t_no.profit < -80
    assert t_be.profit == pytest.approx(0.0, abs=1.0)
