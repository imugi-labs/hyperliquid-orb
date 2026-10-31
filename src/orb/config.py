"""Typed configuration: YAML for settings, environment for secrets."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

INTERVALS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


class MarketCfg(BaseModel):
    coins: list[str] = Field(default_factory=lambda: ["BTC"], min_length=1)
    interval: str = "1h"
    poll_seconds: int = Field(30, ge=5)

    @field_validator("interval")
    @classmethod
    def _interval(cls, v: str) -> str:
        if v not in INTERVALS:
            raise ValueError(f"interval must be one of {sorted(INTERVALS)}")
        return v

    @field_validator("coins")
    @classmethod
    def _coins(cls, v: list[str]) -> list[str]:
        return [c.strip().upper() for c in v]

    @property
    def interval_seconds(self) -> int:
        return INTERVALS[self.interval]


class StrategyCfg(BaseModel):
    name: str = "ema_retest"
    params: dict[str, Any] = Field(default_factory=dict)


class RiskCfg(BaseModel):
    starting_equity: float = Field(1000.0, gt=0)
    risk_per_trade: float = Field(0.01, gt=0, le=0.05)
    max_leverage: float = Field(3.0, gt=0, le=20)
    max_open_positions: int = Field(2, ge=1)
    daily_loss_limit: float = Field(0.03, gt=0, lt=1)
    max_drawdown_kill: float = Field(0.20, gt=0, lt=1)


class ExecutionCfg(BaseModel):
    taker_fee: float = Field(0.00045, ge=0, lt=0.01)
    slippage_bps: float = Field(3.0, ge=0)
    market_slippage: float = Field(0.01, gt=0, le=0.1)


class BuilderCfg(BaseModel):
    enabled: bool = False
    address: str = ""
    fee_tenths_bps: int = Field(10, ge=0, le=100)  # protocol cap on perps: 0.1% = 100

    @model_validator(mode="after")
    def _check(self) -> BuilderCfg:
        if self.enabled and not (self.address.startswith("0x") and len(self.address) == 42):
            raise ValueError("builder.address must be a 0x-prefixed 42-char address when enabled")
        return self

    @property
    def max_fee_rate_str(self) -> str:
        """Format for approveBuilderFee, e.g. 10 tenths-bps -> '0.01%'."""
        return f"{self.fee_tenths_bps / 1000:g}%"


class NotifyCfg(BaseModel):
    telegram: bool = False


class StorageCfg(BaseModel):
    data_dir: str = "data"
    state_file: str = "state/state.json"
    log_file: str = "logs/orb.log"


class Config(BaseModel):
    network: Literal["mainnet", "testnet"] = "mainnet"
    mode: Literal["backtest", "paper", "live"] = "paper"
    market: MarketCfg = Field(default_factory=MarketCfg)
    strategy: StrategyCfg = Field(default_factory=StrategyCfg)
    risk: RiskCfg = Field(default_factory=RiskCfg)
    execution: ExecutionCfg = Field(default_factory=ExecutionCfg)
    builder: BuilderCfg = Field(default_factory=BuilderCfg)
    notifications: NotifyCfg = Field(default_factory=NotifyCfg)
    storage: StorageCfg = Field(default_factory=StorageCfg)

    @property
    def api_url(self) -> str:
        return "https://api.hyperliquid.xyz" if self.network == "mainnet" else "https://api.hyperliquid-testnet.xyz"


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader (no extra dependency). Existing env vars win."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_config(path: str | Path | None) -> Config:
    if path is None:
        return Config()
    data = yaml.safe_load(Path(path).read_text()) or {}
    return Config.model_validate(data)
