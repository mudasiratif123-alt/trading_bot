"""
trade_manager.py — $2 fixed risk, R-based trailing stop
"""

from dataclasses import dataclass, field
from typing import Optional
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class TradeDirection(Enum):
    BUY  = "BUY"
    SELL = "SELL"


class TradeStatus(Enum):
    OPEN      = "OPEN"
    CLOSED_SL = "CLOSED_SL"   # stop loss hit (full loss)
    CLOSED_BE = "CLOSED_BE"   # closed at breakeven
    CLOSED_TP = "CLOSED_TP"   # closed at trailing stop (profit)


@dataclass
class Trade:
    direction:             TradeDirection
    entry_price:           float
    stop_loss:             float
    quantity:              float
    risk_usd:              float
    zone_id:               int
    symbol:                str

    initial_stop_distance: float = 0.0
    current_stop_loss:     float = 0.0
    current_r:             int   = 0
    status:                TradeStatus = TradeStatus.OPEN
    r_targets:             list  = field(default_factory=list)

    def __post_init__(self):
        self.initial_stop_distance = abs(self.entry_price - self.stop_loss)
        self.current_stop_loss     = self.stop_loss
        self._build_r_targets()

    def _build_r_targets(self):
        d = self.initial_stop_distance
        if self.direction == TradeDirection.BUY:
            self.r_targets = [self.entry_price + d * r for r in range(1, 20)]
        else:
            self.r_targets = [self.entry_price - d * r for r in range(1, 20)]

    @property
    def is_open(self) -> bool:
        return self.status == TradeStatus.OPEN

    def to_dict(self) -> dict:
        return {
            "symbol":        self.symbol,
            "zone_id":       self.zone_id,
            "direction":     self.direction.value,
            "entry_price":   self.entry_price,
            "stop_loss":     self.stop_loss,
            "current_sl":    self.current_stop_loss,
            "quantity":      self.quantity,
            "risk_usd":      self.risk_usd,
            "current_r":     self.current_r,
            "status":        self.status.value,
        }


class TradeManager:
    def __init__(self, risk_per_trade_usd: float = 2.0):
        self.risk_usd = risk_per_trade_usd
        self.trade: Optional[Trade] = None

    def open_trade(self, direction: TradeDirection, entry: float,
                   stop_loss: float, zone_id: int, symbol: str) -> Trade:
        stop_dist = abs(entry - stop_loss)
        if stop_dist == 0:
            raise ValueError("Stop distance is zero")

        qty        = self.risk_usd / stop_dist
        actual_risk = qty * stop_dist

        self.trade = Trade(
            direction   = direction,
            entry_price = entry,
            stop_loss   = stop_loss,
            quantity    = qty,
            risk_usd    = actual_risk,
            zone_id     = zone_id,
            symbol      = symbol,
        )
        logger.info(f"Trade OPEN: {direction.value} {symbol} @ {entry} SL={stop_loss} Qty={qty:.6f}")
        return self.trade

    def update(self, price: float) -> dict:
        """Returns events dict. Call on every candle close."""
        events = {"sl_hit": False, "r_reached": None, "sl_moved_to": None, "be_reached": False}
        t = self.trade
        if not t or not t.is_open:
            return events

        # Check SL hit
        if t.direction == TradeDirection.BUY and price <= t.current_stop_loss:
            t.status = TradeStatus.CLOSED_SL if t.current_r == 0 else (
                TradeStatus.CLOSED_BE if t.current_r == 1 else TradeStatus.CLOSED_TP)
            events["sl_hit"] = True
            return events

        if t.direction == TradeDirection.SELL and price >= t.current_stop_loss:
            t.status = TradeStatus.CLOSED_SL if t.current_r == 0 else (
                TradeStatus.CLOSED_BE if t.current_r == 1 else TradeStatus.CLOSED_TP)
            events["sl_hit"] = True
            return events

        # Check R levels
        new_r = self._highest_r_reached(price, t)
        if new_r is not None and new_r > t.current_r:
            t.current_r     = new_r
            new_sl          = self._trailing_sl(t, new_r)
            t.current_stop_loss = new_sl
            events["r_reached"]   = new_r
            events["sl_moved_to"] = new_sl
            events["be_reached"]  = (new_r == 1)

        return events

    def _highest_r_reached(self, price: float, t: Trade) -> Optional[int]:
        for r in range(len(t.r_targets), 0, -1):
            target = t.r_targets[r - 1]
            if t.direction == TradeDirection.BUY  and price >= target:
                return r
            if t.direction == TradeDirection.SELL and price <= target:
                return r
        return None

    def _trailing_sl(self, t: Trade, new_r: int) -> float:
        """1R→entry, 2R→1R price, 3R→2R price, ..."""
        if new_r == 1:
            return t.entry_price
        return t.r_targets[new_r - 2]

    @property
    def is_trade_open(self) -> bool:
        return self.trade is not None and self.trade.is_open
