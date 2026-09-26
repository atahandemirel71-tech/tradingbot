"""Thin wrapper around the MetaTrader5 Python package.

The MetaTrader5 package only runs on Windows next to an installed MT5 terminal,
so it is imported lazily; the rest of the bot can be tested anywhere.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from .risk import SymbolSpec
from .strategy import Signal

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Position:
    ticket: int
    symbol: str
    side: Signal
    volume: float
    price_open: float
    profit: float
    sl: float = 0.0
    tp: float = 0.0
    time: datetime | None = None   # open time (broker server time)


@dataclass(frozen=True)
class Account:
    login: int
    balance: float
    equity: float
    currency: str
    trade_allowed: bool


class MT5Client:
    def __init__(self, login=None, password=None, server=None, terminal_path=None):
        import MetaTrader5 as mt5  # noqa: N813 - official package name

        self.mt5 = mt5
        self._login = login
        self._password = password
        self._server = server
        self._terminal_path = terminal_path
        self._connected = False

    # ---------- connection ----------
    def connect(self) -> bool:
        kwargs = {}
        if self._terminal_path:
            kwargs["path"] = self._terminal_path
        if self._login:
            kwargs.update(login=self._login, password=self._password or "", server=self._server or "")
        if not self.mt5.initialize(**kwargs):
            log.error("MT5 initialize failed: %s", self.mt5.last_error())
            self._connected = False
            return False
        info = self.mt5.terminal_info()
        acc = self.mt5.account_info()
        if info is None or acc is None:
            log.error("MT5 connected but no terminal/account info: %s", self.mt5.last_error())
            self._connected = False
            return False
        log.info("Connected to MT5 '%s', account %s on %s", info.name, acc.login, acc.server)
        if not info.trade_allowed:
            log.warning("Algo Trading is DISABLED in the MT5 terminal - enable the 'Algo Trading' button")
        self._connected = True
        return True

    def ensure_connected(self) -> bool:
        if self._connected and self.mt5.terminal_info() is not None and self.mt5.account_info() is not None:
            return True
        log.warning("MT5 connection lost, reconnecting...")
        self.shutdown()
        return self.connect()

    def shutdown(self) -> None:
        try:
            self.mt5.shutdown()
        finally:
            self._connected = False

    # ---------- data ----------
    def account(self) -> Account | None:
        a = self.mt5.account_info()
        if a is None:
            return None
        term = self.mt5.terminal_info()
        allowed = bool(a.trade_allowed and a.trade_expert and term is not None and term.trade_allowed)
        return Account(a.login, float(a.balance), float(a.equity), a.currency, allowed)

    def timeframe(self, name: str) -> int:
        return getattr(self.mt5, f"TIMEFRAME_{name}")

    def closed_bars(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame | None:
        # Start at position 1 to skip the bar that is still forming.
        rates = self.mt5.copy_rates_from_pos(symbol, self.timeframe(timeframe), 1, count)
        if rates is None or len(rates) == 0:
            log.warning("%s: no rate data (%s)", symbol, self.mt5.last_error())
            return None
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df

    def prepare_symbol(self, symbol: str) -> bool:
        info = self.mt5.symbol_info(symbol)
        if info is None:
            log.error("%s: unknown symbol at this broker", symbol)
            return False
        if not info.visible and not self.mt5.symbol_select(symbol, True):
            log.error("%s: could not add to Market Watch", symbol)
            return False
        return True

    def symbol_spec(self, symbol: str) -> SymbolSpec | None:
        i = self.mt5.symbol_info(symbol)
        if i is None:
            return None
        return SymbolSpec(
            tick_size=i.trade_tick_size,
            tick_value=i.trade_tick_value,
            volume_min=i.volume_min,
            volume_max=i.volume_max,
            volume_step=i.volume_step,
            point=i.point,
            digits=i.digits,
            stops_level=i.trade_stops_level,
        )

    def can_trade_symbol(self, symbol: str) -> bool:
        i = self.mt5.symbol_info(symbol)
        return i is not None and i.trade_mode == self.mt5.SYMBOL_TRADE_MODE_FULL

    def quote(self, symbol: str) -> tuple[float, float] | None:
        t = self.mt5.symbol_info_tick(symbol)
        if t is None or t.bid <= 0 or t.ask <= 0:
            return None
        return float(t.bid), float(t.ask)

    def server_time(self, symbol: str) -> datetime | None:
        """Broker server wall-clock time of the latest tick (MT5 reports it as a UTC-like timestamp)."""
        t = self.mt5.symbol_info_tick(symbol)
        if t is None or not t.time:
            return None
        return datetime.fromtimestamp(t.time, tz=timezone.utc)

    def positions(self, magic: int) -> list[Position]:
        raw = self.mt5.positions_get()
        if raw is None:
            return []
        out = []
        for p in raw:
            if p.magic != magic:
                continue
            side = Signal.BUY if p.type == self.mt5.POSITION_TYPE_BUY else Signal.SELL
            out.append(Position(p.ticket, p.symbol, side, p.volume, p.price_open, p.profit,
                                p.sl, p.tp, datetime.fromtimestamp(p.time, tz=timezone.utc)))
        return out

    # ---------- orders ----------
    def _filling_mode(self, symbol: str) -> int:
        i = self.mt5.symbol_info(symbol)
        flags = i.filling_mode if i is not None else 0
        if flags & 1:  # SYMBOL_FILLING_FOK
            return self.mt5.ORDER_FILLING_FOK
        if flags & 2:  # SYMBOL_FILLING_IOC
            return self.mt5.ORDER_FILLING_IOC
        return self.mt5.ORDER_FILLING_RETURN

    def _send(self, request: dict) -> bool:
        result = self.mt5.order_send(request)
        if result is None:
            log.error("order_send returned None: %s", self.mt5.last_error())
            return False
        if result.retcode != self.mt5.TRADE_RETCODE_DONE:
            log.error("Order rejected: retcode=%s comment=%s", result.retcode, result.comment)
            return False
        log.info("Order filled: ticket=%s volume=%s price=%s", result.order, result.volume, result.price)
        return True

    def open_position(self, symbol, side: Signal, volume, sl, tp, magic, deviation, comment="") -> bool:
        q = self.quote(symbol)
        if q is None:
            return False
        bid, ask = q
        is_buy = side is Signal.BUY
        return self._send({
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume),
            "type": self.mt5.ORDER_TYPE_BUY if is_buy else self.mt5.ORDER_TYPE_SELL,
            "price": ask if is_buy else bid,
            "sl": float(sl),
            "tp": float(tp),
            "deviation": int(deviation),
            "magic": int(magic),
            "comment": comment[:31],
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(symbol),
        })

    def modify_stops(self, pos: Position, sl: float, tp: float) -> bool:
        return self._send({
            "action": self.mt5.TRADE_ACTION_SLTP,
            "symbol": pos.symbol,
            "position": int(pos.ticket),
            "sl": float(sl),
            "tp": float(tp),
        })

    def close_position(self, pos: Position, magic, deviation) -> bool:
        q = self.quote(pos.symbol)
        if q is None:
            return False
        bid, ask = q
        closing_buy = pos.side is Signal.BUY
        return self._send({
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": float(pos.volume),
            "type": self.mt5.ORDER_TYPE_SELL if closing_buy else self.mt5.ORDER_TYPE_BUY,
            "position": int(pos.ticket),
            "price": bid if closing_buy else ask,
            "deviation": int(deviation),
            "magic": int(magic),
            "comment": "bot close",
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(pos.symbol),
        })
