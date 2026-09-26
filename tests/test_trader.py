from datetime import datetime, timezone

import numpy as np
import pandas as pd

import pytest

from bot.config import BotConfig
from bot.hours import TradingHours
from bot.mt5_client import Account, Position
from bot.risk import RiskParams, SymbolSpec
from bot.strategy import Signal, StrategyParams, generate_signal
from bot.trader import Trader

PARAMS = StrategyParams(fast_ema=3, slow_ema=8, trend_ema=20, rsi_period=5, atr_period=5, use_adx_filter=False,
                        sl_atr_mult=1.5, tp_atr_mult=3.0, trailing_atr_mult=0, breakeven_at_r=0)
NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)  # a Thursday
FRIDAY_EVENING = datetime(2026, 1, 2, 21, tzinfo=timezone.utc)


def make_bars(close):
    close = pd.Series(close, dtype=float)
    return pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=len(close), freq="15min", tz="UTC"),
        "open": close.shift(1).fillna(close.iloc[0]),
        "high": close + 0.0005,
        "low": close - 0.0005,
        "close": close,
    })


def buy_series():
    close = list(np.linspace(1.00, 1.10, 80)) + list(np.linspace(1.10, 1.095, 6))
    for extra in np.linspace(1.096, 1.12, 10):
        close.append(extra)
        if generate_signal(make_bars(close), PARAMS).signal is Signal.BUY:
            return close
    raise AssertionError("fixture never produced a BUY")


class FakeClient:
    def __init__(self, bars, positions=(), equity=10_000.0):
        self.bars = bars
        self._positions = list(positions)
        self.equity = equity
        self.opened, self.closed, self.modified = [], [], []

    def ensure_connected(self):
        return True

    def account(self):
        return Account(1, 10_000.0, self.equity, "USD", True)

    def closed_bars(self, symbol, timeframe, count):
        return self.bars

    def positions(self, magic):
        return list(self._positions)

    def can_trade_symbol(self, symbol):
        return True

    def symbol_spec(self, symbol):
        return SymbolSpec(0.00001, 1.0, 0.01, 100.0, 0.01, point=0.00001, digits=5, stops_level=10)

    def quote(self, symbol):
        last = float(self.bars["close"].iloc[-1])
        return last - 0.00005, last + 0.00005

    def open_position(self, symbol, side, volume, sl, tp, magic, deviation, comment=""):
        self.opened.append((symbol, side, volume, sl, tp))
        self._positions.append(Position(99, symbol, side, volume, 0.0, 0.0))
        return True

    def modify_stops(self, pos, sl, tp):
        self.modified.append((pos.ticket, sl, tp))
        return True

    def close_position(self, pos, magic, deviation):
        self.closed.append(pos.ticket)
        self._positions = [p for p in self._positions if p.ticket != pos.ticket]
        return True


@pytest.fixture(autouse=True)
def _state_in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def make_cfg(**kw):
    kw.setdefault("risk_per_trade_pct", 1.0)
    return BotConfig(symbols=["EURUSD"], dry_run=False, strategy=PARAMS, risk=RiskParams(**kw))


def test_opens_buy_with_sl_below_and_tp_above():
    client = FakeClient(make_bars(buy_series()))
    Trader(make_cfg(), client).tick(NOW)
    assert len(client.opened) == 1
    symbol, side, volume, sl, tp = client.opened[0]
    ask = client.quote("EURUSD")[1]
    assert side is Signal.BUY and volume > 0 and sl < ask < tp


def test_same_bar_is_not_traded_twice():
    client = FakeClient(make_bars(buy_series()))
    t = Trader(make_cfg(), client)
    t.tick(NOW)
    client._positions.clear()
    t.tick(NOW)
    assert len(client.opened) == 1


def test_reverses_opposite_position():
    client = FakeClient(make_bars(buy_series()), [Position(5, "EURUSD", Signal.SELL, 0.1, 1.1, -3.0)])
    Trader(make_cfg(), client).tick(NOW)
    assert client.closed == [5]
    assert client.opened[0][1] is Signal.BUY


def test_dry_run_sends_nothing():
    client = FakeClient(make_bars(buy_series()), [Position(5, "EURUSD", Signal.SELL, 0.1, 1.1, -3.0)])
    cfg = make_cfg()
    cfg.dry_run = True
    Trader(cfg, client).tick(NOW)
    assert client.opened == [] and client.closed == []


def test_max_open_positions_respected():
    others = [Position(i, f"SYM{i}", Signal.BUY, 0.1, 1.0, 0.0) for i in range(3)]
    client = FakeClient(make_bars(buy_series()), others)
    Trader(make_cfg(max_open_positions=3), client).tick(NOW)
    assert client.opened == []


def test_daily_loss_limit_blocks_new_trades():
    client = FakeClient(make_bars(buy_series()))
    t = Trader(make_cfg(max_daily_loss_pct=5.0), client)
    t.guard.update(NOW.date(), 10_000.0)
    client.equity = 9_400.0
    t.tick(NOW)
    assert client.opened == []


def test_daily_loss_limit_closes_open_positions():
    client = FakeClient(make_bars(buy_series()), [Position(5, "EURUSD", Signal.SELL, 0.1, 1.1, -600.0)])
    t = Trader(make_cfg(max_daily_loss_pct=5.0), client)
    t.guard.update(NOW.date(), 10_000.0)
    client.equity = 9_400.0
    t.tick(NOW)
    assert client.closed == [5] and client.opened == []


def test_total_loss_limit_halts_for_good():
    client = FakeClient(make_bars(buy_series()), [Position(5, "EURUSD", Signal.SELL, 0.1, 1.1, -900.0)])
    t = Trader(make_cfg(max_total_loss_pct=8.0, account_start_balance=10_000.0, max_daily_loss_pct=0), client)
    client.equity = 9_150.0
    t.tick(NOW)
    assert client.closed == [5]
    client.equity = 10_000.0          # even after recovering, the bot stays stopped
    t._last_bar.clear()
    t.tick(NOW)
    assert client.opened == []


def test_start_balance_remembered_across_restarts():
    client = FakeClient(make_bars(buy_series()))
    Trader(make_cfg(), client).tick(NOW)
    client.equity = 9_000.0            # a restarted bot must not take today's equity as the new start
    client._positions.clear()
    t2 = Trader(make_cfg(max_daily_loss_pct=0), client)
    t2.tick(NOW)
    assert t2.total_guard.start_balance == 10_000.0


def test_weekend_close_and_no_new_trades_on_friday_evening():
    client = FakeClient(make_bars(buy_series()), [Position(5, "GBPUSD", Signal.BUY, 0.1, 1.3, 12.0)])
    Trader(make_cfg(), client).tick(FRIDAY_EVENING)
    assert client.closed == [5] and client.opened == []


def test_session_filter_blocks_entries_outside_hours():
    client = FakeClient(make_bars(buy_series()))
    cfg = make_cfg()
    cfg.hours = TradingHours(use_session_filter=True, session_start_hour=14, session_end_hour=18)
    Trader(cfg, client).tick(NOW)  # 12:00
    assert client.opened == []


def test_wide_spread_blocks_entry():
    client = FakeClient(make_bars(buy_series()))
    Trader(make_cfg(max_spread_points=5), client).tick(NOW)
    assert client.opened == []


def test_trailing_stop_moved_on_new_bar():
    from dataclasses import replace
    close = list(np.linspace(1.00, 1.10, 300))
    bars = make_bars(close)
    entry_idx = 250
    pos = Position(7, "EURUSD", Signal.BUY, 0.1, close[entry_idx], 50.0, sl=close[entry_idx] - 0.003, tp=0.0,
                   time=bars["time"].iloc[entry_idx].to_pydatetime())
    client = FakeClient(bars, [pos])
    cfg = make_cfg()
    cfg.strategy = replace(PARAMS, tp_atr_mult=0, trailing_atr_mult=3.0, breakeven_at_r=1.0)
    Trader(cfg, client).tick(NOW)
    assert client.modified, "stop should have been trailed"
    ticket, new_sl, tp = client.modified[0]
    assert ticket == 7 and new_sl > pos.sl and new_sl >= pos.price_open and tp == 0.0
    assert new_sl < client.quote("EURUSD")[0]
