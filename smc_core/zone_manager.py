"""
zone_manager.py — Multiple zones per symbol, manual entry only
"""

from dataclasses import dataclass, field
from typing import Optional
import logging

logger = logging.getLogger(__name__)

VALID_ZONE_TYPES = {"Support", "Resistance", "Demand", "Supply"}


@dataclass
class Zone:
    zone_id:   int
    zone_low:  float
    zone_high: float
    zone_type: str
    active:    bool = True

    interaction_started:  bool           = False
    interaction_low:      Optional[float] = None
    interaction_high:     Optional[float] = None
    rejected:             bool           = False

    def price_touched(self, low: float, high: float) -> bool:
        return low <= self.zone_high and high >= self.zone_low

    def price_inside(self, price: float) -> bool:
        return self.zone_low <= price <= self.zone_high

    def reset_interaction(self):
        self.interaction_started = False
        self.interaction_low     = None
        self.interaction_high    = None
        self.rejected            = False


class ZoneManager:
    """Manages multiple zones for one symbol."""

    def __init__(self, aggressive_pct: float = 0.5):
        self.aggressive_pct = aggressive_pct
        self.zones: list[Zone] = []
        self._next_id = 1

    def add_zone(self, zone_low: float, zone_high: float, zone_type: str) -> Zone:
        if zone_type not in VALID_ZONE_TYPES:
            raise ValueError(f"Zone type must be one of: {VALID_ZONE_TYPES}")
        if zone_low >= zone_high:
            raise ValueError("zone_low must be less than zone_high")

        zone = Zone(
            zone_id   = self._next_id,
            zone_low  = zone_low,
            zone_high = zone_high,
            zone_type = zone_type,
        )
        self.zones.append(zone)
        self._next_id += 1
        logger.info(f"Zone #{zone.zone_id} added: {zone_type} [{zone_low} – {zone_high}]")
        return zone

    def remove_zone(self, zone_id: int) -> bool:
        before = len(self.zones)
        self.zones = [z for z in self.zones if z.zone_id != zone_id]
        return len(self.zones) < before

    def clear_zones(self):
        self.zones.clear()

    def list_zones(self) -> list[Zone]:
        return [z for z in self.zones if z.active]

    def update(self, open_: float, high: float, low: float, close: float) -> list[dict]:
        """
        Returns list of zone events — one per zone that has activity.
        Each event: {zone, touched, inside, rejected, valid}
        """
        events = []
        for z in self.zones:
            if not z.active:
                continue

            event = {"zone": z, "touched": False, "inside": False,
                     "rejected": False, "valid": False}

            if z.rejected:
                event["rejected"] = True
                events.append(event)
                continue

            touched = z.price_touched(low, high)
            inside  = z.price_inside(close)

            event["touched"] = touched
            event["inside"]  = inside

            if not touched:
                if z.interaction_started:
                    z.reset_interaction()
                events.append(event)
                continue

            if not z.interaction_started:
                z.interaction_started = True
                z.interaction_low     = low
                z.interaction_high    = high

            z.interaction_low  = min(z.interaction_low,  low)
            z.interaction_high = max(z.interaction_high, high)

            candle_move_pct = abs(close - open_) / open_ * 100
            if candle_move_pct > self.aggressive_pct:
                z.rejected     = True
                event["rejected"] = True
                logger.warning(f"Zone #{z.zone_id} REJECTED — candle move {candle_move_pct:.2f}%")
                events.append(event)
                continue

            event["valid"] = True
            events.append(event)

        return events
