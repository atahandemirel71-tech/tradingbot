import numpy as np
import pandas as pd
import pytest

from bot.strategy import Signal, StrategyParams, adx, generate_signal, rsi, stop_levels, trailed_stop


def bars_from_close(close):
    close = pd.Series(close, dtype=float)
    return pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=len(close), freq="15min", tz="UTC"),
        "open": close.shift(1).fillna(close.iloc[0]),
        "high": close + 0.0005,
        "low": close - 0.0005,
        "close": close,
    })


PARAMS = StrategyParams(fast_ema=3, slow_ema=8, trend_ema=20, rsi_period=5, atr_period=5, use_adx_filter=False,
                        sl_atr_mult=1.5, tp_atr_mult=3.0, trailing_atr_mult=0, breakeven_at_r=0)


def test_not_enough_bars():
    res = generate_signal(bars_from_close([1.0] * 10), PARAMS)
    assert res.signal is Signal.NONE


def test_buy_on_bullish_cross_above_trend():
    # Long uptrend, a short dip that pulls fast EMA below slow, then a rebound -> bullish cross.
    up = list(np.linspace(1.00, 1.10, 80))
    dip = list(np.linspace(1.10, 1.095, 6))
    close = up + dip
    params = PARAMS
    # Find the first bar after the dip where a BUY appears.
    signals = []
    for extra in np.linspace(1.096, 1.12, 10):
        close.append(extra)
        signals.append(generate_signal(bars_from_close(close), params).signal)
    assert Signal.BUY in signals
    assert Signal.SELL not in signals


def test_sell_on_bearish_cross_below_trend():
    down = list(np.linspace(1.10, 1.00, 80))
    bounce = list(np.linspace(1.00, 1.005, 6))
    close = down + bounce
    signals = []
    for extra in np.linspace(1.004, 0.98, 10):
        close.append(extra)
        signals.append(generate_signal(bars_from_close(close), PARAMS).signal)
    assert Signal.SELL in signals
    assert Signal.BUY not in signals


def test_trend_filter_blocks_counter_trend_buy():
    # Downtrend with a small bounce: fast crosses above slow but price is still under trend EMA.
    down = list(np.linspace(1.20, 1.00, 80))
    close = down + list(np.linspace(1.0, 1.006, 8))
    results = [generate_signal(bars_from_close(close[:i]), PARAMS) for i in range(81, len(close) + 1)]
    assert all(r.signal is not Signal.BUY for r in results)


def test_rsi_bounds():
    close = pd.Series(np.random.default_rng(1).normal(0, 1, 300).cumsum() + 100)
    values = rsi(close, 14).dropna()
    assert ((values >= 0) & (values <= 100)).all()
    assert rsi(pd.Series(np.arange(50, dtype=float)), 14).iloc[-1] == pytest.approx(100.0)


def test_stop_levels():
    p = StrategyParams(sl_atr_mult=1.5, tp_atr_mult=3.0)
    assert stop_levels(Signal.BUY, 1.1, 0.001, p) == pytest.approx((1.0985, 1.103))
    assert stop_levels(Signal.SELL, 1.1, 0.001, p) == pytest.approx((1.1015, 1.097))


def test_adx_high_in_trend_low_in_range():
    trend = bars_from_close(np.linspace(1.0, 1.2, 200))
    rng = np.random.default_rng(0)
    ranging = bars_from_close(1.1 + rng.normal(0, 0.0003, 200))
    assert adx(trend, 14).iloc[-1] > 40
    assert adx(ranging, 14).iloc[-1] < 25


def test_adx_filter_blocks_cross_without_trend():
    close = list(np.linspace(1.00, 1.10, 80)) + list(np.linspace(1.10, 1.095, 6))
    strict = StrategyParams(fast_ema=3, slow_ema=8, trend_ema=20, rsi_period=5, atr_period=5,
                            use_adx_filter=True, adx_min=99)
    results = []
    for extra in np.linspace(1.096, 1.12, 10):
        close.append(extra)
        results.append(generate_signal(bars_from_close(close), strict))
    assert all(r.signal is Signal.NONE for r in results)
    assert any("ADX" in r.reason for r in results)


def test_no_take_profit_when_disabled():
    p = StrategyParams(sl_atr_mult=2.0, tp_atr_mult=0)
    assert stop_levels(Signal.BUY, 1.1, 0.001, p) == pytest.approx((1.098, 0.0))


TRAIL = StrategyParams(trailing_atr_mult=3.0, breakeven_at_r=1.0)


def test_trailing_moves_only_in_favour():
    # Buy at 1.1000, SL 1.0980 (risk 0.0020), ATR 0.0010.
    assert trailed_stop(Signal.BUY, 1.1, 1.098, 0.002, 1.1010, 0.001, TRAIL) == pytest.approx(1.098)   # not yet
    assert trailed_stop(Signal.BUY, 1.1, 1.098, 0.002, 1.1020, 0.001, TRAIL) == pytest.approx(1.1)     # breakeven at 1R
    assert trailed_stop(Signal.BUY, 1.1, 1.1, 0.002, 1.1060, 0.001, TRAIL) == pytest.approx(1.103)     # trails 3 ATR
    assert trailed_stop(Signal.BUY, 1.1, 1.103, 0.002, 1.1040, 0.001, TRAIL) == pytest.approx(1.103)   # never back


def test_trailing_for_sells_mirrors_buys():
    assert trailed_stop(Signal.SELL, 1.1, 1.102, 0.002, 1.0980, 0.001, TRAIL) == pytest.approx(1.1)
    assert trailed_stop(Signal.SELL, 1.1, 1.1, 0.002, 1.0940, 0.001, TRAIL) == pytest.approx(1.097)


def test_trailing_off_keeps_stop():
    off = StrategyParams(trailing_atr_mult=0, breakeven_at_r=0)
    assert trailed_stop(Signal.BUY, 1.1, 1.098, 0.002, 1.2, 0.001, off) == 1.098
