"""Load settings from config.yaml and credentials from .env / environment."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from .hours import TradingHours
from .risk import RiskParams
from .strategy import StrategyParams

VALID_TIMEFRAMES = {"M1", "M5", "M15", "M30", "H1", "H4", "D1"}


@dataclass
class Credentials:
    login: int | None = None
    password: str | None = None
    server: str | None = None
    terminal_path: str | None = None


@dataclass
class BotConfig:
    symbols: list[str] = field(default_factory=lambda: ["EURUSD"])
    timeframe: str = "M15"
    magic_number: int = 20260926
    poll_seconds: int = 10
    dry_run: bool = True
    close_on_opposite_signal: bool = True
    deviation_points: int = 20
    log_file: str = "logs/bot.log"
    state_file: str = "logs/state.json"
    hours: TradingHours = field(default_factory=TradingHours)
    strategy: StrategyParams = field(default_factory=StrategyParams)
    risk: RiskParams = field(default_factory=RiskParams)
    credentials: Credentials = field(default_factory=Credentials)


def _build(cls, data: dict[str, Any] | None):
    data = data or {}
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"Unknown {cls.__name__} settings: {sorted(unknown)}")
    return cls(**data)


def load_config(path: str | Path = "config.yaml") -> BotConfig:
    path = Path(path)
    raw: dict[str, Any] = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    strategy = _build(StrategyParams, raw.pop("strategy", None))
    risk = _build(RiskParams, raw.pop("risk", None))
    hours = _build(TradingHours, raw.pop("hours", None))
    cfg = _build(BotConfig, raw)
    cfg.strategy = strategy
    cfg.risk = risk
    cfg.hours = hours
    cfg.credentials = _load_credentials(path.parent)
    _validate(cfg)
    return cfg


def _load_credentials(base_dir: Path) -> Credentials:
    try:
        from dotenv import load_dotenv

        load_dotenv(base_dir / ".env")
    except ImportError:
        pass
    login = os.getenv("MT5_LOGIN")
    return Credentials(
        login=int(login) if login else None,
        password=os.getenv("MT5_PASSWORD") or None,
        server=os.getenv("MT5_SERVER") or None,
        terminal_path=os.getenv("MT5_PATH") or None,
    )


def _validate(cfg: BotConfig) -> None:
    if not cfg.symbols:
        raise ValueError("config: 'symbols' must list at least one symbol")
    if cfg.timeframe not in VALID_TIMEFRAMES:
        raise ValueError(f"config: timeframe must be one of {sorted(VALID_TIMEFRAMES)}")
    s = cfg.strategy
    if s.fast_ema >= s.slow_ema:
        raise ValueError("config: strategy.fast_ema must be smaller than strategy.slow_ema")
    if s.sl_atr_mult <= 0 or s.tp_atr_mult <= 0:
        raise ValueError("config: ATR multipliers must be positive")
    r = cfg.risk
    if not 0 < r.risk_per_trade_pct <= 5:
        raise ValueError("config: risk.risk_per_trade_pct must be between 0 and 5")
    if r.max_open_positions < 1:
        raise ValueError("config: risk.max_open_positions must be at least 1")
    if r.max_total_loss_pct < 0 or r.max_daily_loss_pct < 0:
        raise ValueError("config: loss limits cannot be negative")
    if s.use_adx_filter and s.adx_min <= 0:
        raise ValueError("config: strategy.adx_min must be positive")
    cfg.hours.validate()
    if cfg.poll_seconds < 1:
        raise ValueError("config: poll_seconds must be at least 1")
