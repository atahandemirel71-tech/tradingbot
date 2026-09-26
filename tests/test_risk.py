from datetime import date

import pytest

from bot.risk import DailyLossGuard, SymbolSpec, lot_size

EURUSD = SymbolSpec(tick_size=0.00001, tick_value=1.0, volume_min=0.01, volume_max=100.0, volume_step=0.01)


def test_lot_size_risks_the_right_amount():
    # 10 000 balance, 1% = 100 risk, 20 pip stop = 200 ticks * 1.0 = 200 per lot -> 0.5 lots.
    assert lot_size(10_000, 1.0, 0.0020, EURUSD) == pytest.approx(0.5)


def test_lot_size_rounds_down_to_step():
    # 100 / (0.0030/0.00001 * 1.0) = 0.3333 -> 0.33
    assert lot_size(10_000, 1.0, 0.0030, EURUSD) == pytest.approx(0.33)


def test_lot_size_zero_when_below_minimum():
    assert lot_size(50, 1.0, 0.0100, EURUSD) == 0.0


def test_lot_size_capped_at_max():
    spec = SymbolSpec(0.00001, 1.0, 0.01, 1.0, 0.01)
    assert lot_size(10_000_000, 1.0, 0.0010, spec) == 1.0


@pytest.mark.parametrize("bad", [(0, 1, 0.001), (1000, 0, 0.001), (1000, 1, 0)])
def test_lot_size_invalid_inputs(bad):
    assert lot_size(*bad, EURUSD) == 0.0


def test_daily_loss_guard():
    g = DailyLossGuard(5.0)
    g.update(date(2026, 1, 1), 10_000)
    assert g.trading_allowed(9_600)
    assert not g.trading_allowed(9_500)
    # Same day: start equity is not reset by a later update.
    g.update(date(2026, 1, 1), 9_400)
    assert not g.trading_allowed(9_400)
    # New day resets.
    g.update(date(2026, 1, 2), 9_400)
    assert g.trading_allowed(9_400)
