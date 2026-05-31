"""
smc_engine.py — Pine Script BOS/CHOCH exact replication
Internal pivot size = 5 (hardcoded in Pine Script)
Swing pivot size   = 50 (default in Pine Script)
"""

from dataclasses import dataclass
from typing import Optional
import logging

logger = logging.getLogger(__name__)

BULLISH     = 1
BEARISH     = -1
BULLISH_LEG = 1
BEARISH_LEG = 0


@dataclass
class PivotPoint:
    current_level: Optional[float] = None
    last_level:    Optional[float] = None
    crossed:       bool            = False
    bar_index:     Optional[int]   = None


@dataclass
class Trend:
    bias: int = 0


@dataclass
class StructureSignal:
    signal_type:   str    # 'BOS' or 'CHOCH'
    direction:     str    # 'bullish' or 'bearish'
    structure:     str    # 'internal' or 'swing'
    level:         float
    bar_index:     int
    pivot_bar_idx: int


class SMCEngine:
    def __init__(self, internal_size: int = 5, swing_size: int = 50):
        self.internal_size = internal_size
        self.swing_size    = swing_size

        self._opens:  list = []
        self._highs:  list = []
        self._lows:   list = []
        self._closes: list = []

        self.swing_high     = PivotPoint()
        self.swing_low      = PivotPoint()
        self.swing_trend    = Trend()
        self.internal_high  = PivotPoint()
        self.internal_low   = PivotPoint()
        self.internal_trend = Trend()

        self._swing_leg_series:    list = []
        self._internal_leg_series: list = []

    def update(self, open_: float, high: float, low: float, close: float) -> list:
        self._opens.append(open_)
        self._highs.append(high)
        self._lows.append(low)
        self._closes.append(close)

        signals = []
        idx = len(self._closes) - 1

        swing_leg    = self._compute_leg_stateful(self._highs, self._lows, self.swing_size,    self._swing_leg_series)
        internal_leg = self._compute_leg_stateful(self._highs, self._lows, self.internal_size, self._internal_leg_series)

        self._swing_leg_series.append(swing_leg)
        self._internal_leg_series.append(internal_leg)

        self._update_pivots(self._swing_leg_series,    self.swing_size,    internal=False, idx=idx)
        self._update_pivots(self._internal_leg_series, self.internal_size, internal=True,  idx=idx)

        if len(self._closes) >= 2:
            signals += self._detect_structure(internal=True,  idx=idx)
            signals += self._detect_structure(internal=False, idx=idx)

        return signals

    def reset(self):
        self.__init__(self.internal_size, self.swing_size)

    @staticmethod
    def _compute_leg_stateful(highs, lows, size, leg_series) -> int:
        prev_leg = leg_series[-1] if leg_series else BEARISH_LEG
        n = len(highs)
        if n < size + 1:
            return prev_leg

        pivot_high   = highs[-(size + 1)]
        pivot_low    = lows[-(size + 1)]
        recent_highs = highs[-size:]
        recent_lows  = lows[-size:]

        if pivot_high > max(recent_highs):
            return BEARISH_LEG
        elif pivot_low < min(recent_lows):
            return BULLISH_LEG
        return prev_leg

    def _update_pivots(self, leg_series, size, internal, idx):
        if len(leg_series) < 2:
            return

        curr_leg = leg_series[-1]
        prev_leg = leg_series[-2]

        if curr_leg == prev_leg:
            return

        pivot_bar = idx - size
        if pivot_bar < 0:
            return

        pivot_low  = (curr_leg - prev_leg) == +1
        pivot_high = (curr_leg - prev_leg) == -1

        if pivot_low:
            p = self.internal_low if internal else self.swing_low
            p.last_level    = p.current_level
            p.current_level = self._lows[pivot_bar]
            p.crossed       = False
            p.bar_index     = pivot_bar
        else:
            p = self.internal_high if internal else self.swing_high
            p.last_level    = p.current_level
            p.current_level = self._highs[pivot_bar]
            p.crossed       = False
            p.bar_index     = pivot_bar

    def _detect_structure(self, internal, idx) -> list:
        signals = []
        closes  = self._closes

        p_high = self.internal_high  if internal else self.swing_high
        p_low  = self.internal_low   if internal else self.swing_low
        t_rend = self.internal_trend if internal else self.swing_trend
        label  = "internal" if internal else "swing"

        if internal:
            extra_bull = (p_high.current_level is not None and
                          self.swing_high.current_level is not None and
                          p_high.current_level != self.swing_high.current_level)
            extra_bear = (p_low.current_level is not None and
                          self.swing_low.current_level is not None and
                          p_low.current_level != self.swing_low.current_level)
        else:
            extra_bull = extra_bear = True

        # Bullish cross
        if (p_high.current_level is not None and not p_high.crossed and extra_bull and
                self._crossover(closes, p_high.current_level)):
            tag            = "CHOCH" if t_rend.bias == BEARISH else "BOS"
            p_high.crossed = True
            t_rend.bias    = BULLISH
            signals.append(StructureSignal(tag, "bullish", label, p_high.current_level, idx, p_high.bar_index or 0))

        # Bearish cross
        if (p_low.current_level is not None and not p_low.crossed and extra_bear and
                self._crossunder(closes, p_low.current_level)):
            tag           = "CHOCH" if t_rend.bias == BULLISH else "BOS"
            p_low.crossed = True
            t_rend.bias   = BEARISH
            signals.append(StructureSignal(tag, "bearish", label, p_low.current_level, idx, p_low.bar_index or 0))

        return signals

    @staticmethod
    def _crossover(closes, level) -> bool:
        return len(closes) >= 2 and closes[-2] < level <= closes[-1]

    @staticmethod
    def _crossunder(closes, level) -> bool:
        return len(closes) >= 2 and closes[-2] > level >= closes[-1]

    def get_state_dict(self) -> dict:
        def p2d(p):
            return {"current_level": p.current_level, "last_level": p.last_level,
                    "crossed": p.crossed, "bar_index": p.bar_index}
        return {
            "swing_high": p2d(self.swing_high), "swing_low": p2d(self.swing_low),
            "swing_trend": {"bias": self.swing_trend.bias},
            "internal_high": p2d(self.internal_high), "internal_low": p2d(self.internal_low),
            "internal_trend": {"bias": self.internal_trend.bias},
            "candle_count": len(self._closes),
        }
