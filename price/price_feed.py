"""
price_feed.py
=============
Fetches live 1-minute candles from MEXC via WebSocket.
Paper mode: uses real market data but places NO orders.
Supports multiple symbols simultaneously.
"""

import asyncio
import json
import logging
import time
from typing import Callable, Optional
import urllib.request

logger = logging.getLogger(__name__)

MEXC_WS_URL   = "wss://contract.mexc.com/edge"
MEXC_REST_URL = "https://contract.mexc.com/api/v1/contract/kline/{symbol}?interval=Min1&limit={limit}"


class Candle:
    __slots__ = ("open", "high", "low", "close", "volume", "ts")

    def __init__(self, o, h, l, c, v, ts):
        self.open, self.high, self.low, self.close, self.volume, self.ts = o, h, l, c, v, ts

    def __repr__(self):
        return f"Candle(O={self.open} H={self.high} L={self.low} C={self.close})"


class PriceFeed:
    """
    Manages WebSocket connections for multiple symbols.
    Calls on_candle(symbol, candle) for each closed 1-minute candle.
    No API key needed — uses public market data.
    """

    def __init__(self):
        self._callbacks: dict[str, Callable] = {}   # symbol → async callback
        self._running = False
        self._last_ts: dict[str, int] = {}

    def subscribe(self, symbol: str, callback: Callable):
        """Register callback for a symbol's candles."""
        self._callbacks[symbol] = callback
        self._last_ts[symbol]   = 0
        logger.info(f"Subscribed to {symbol} candles")

    def unsubscribe(self, symbol: str):
        self._callbacks.pop(symbol, None)
        self._last_ts.pop(symbol, None)

    async def fetch_historical(self, symbol: str, limit: int = 200) -> list[Candle]:
        """
        Fetch historical candles via MEXC REST API.
        No API key needed for public market data.
        """
        url = MEXC_REST_URL.format(symbol=symbol, limit=limit + 1)
        try:
            loop = asyncio.get_event_loop()
            raw  = await loop.run_in_executor(None, lambda: urllib.request.urlopen(url, timeout=10).read())
            data = json.loads(raw)

            candles_raw = data.get("data", {})
            if not candles_raw:
                logger.warning(f"No historical data for {symbol}")
                return []

            # MEXC returns arrays: time, open, high, low, close, vol, ...
            times  = candles_raw.get("time",  [])
            opens  = candles_raw.get("open",  [])
            highs  = candles_raw.get("high",  [])
            lows   = candles_raw.get("low",   [])
            closes = candles_raw.get("close", [])
            vols   = candles_raw.get("vol",   [])

            candles = []
            for i in range(len(times) - 1):   # drop last (open) candle
                candles.append(Candle(
                    float(opens[i]), float(highs[i]), float(lows[i]),
                    float(closes[i]), float(vols[i] if vols else 0), int(times[i])
                ))
            logger.info(f"Fetched {len(candles)} historical candles for {symbol}")
            return candles

        except Exception as e:
            logger.error(f"Historical fetch failed for {symbol}: {e}")
            return []

    async def start(self):
        """Start WebSocket stream. Auto-reconnects."""
        self._running = True
        while self._running:
            try:
                await self._stream()
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
            if self._running:
                logger.info("Reconnecting in 5 seconds...")
                await asyncio.sleep(5)

    def stop(self):
        self._running = False

    async def _stream(self):
        import websockets
        logger.info(f"Connecting to MEXC WebSocket...")

        async with websockets.connect(MEXC_WS_URL, ping_interval=20, ping_timeout=10) as ws:
            logger.info("WebSocket connected ✅")

            # Subscribe to all symbols
            for symbol in list(self._callbacks.keys()):
                sub = {"method": "sub.kline", "param": {"symbol": symbol, "interval": "Min1"}}
                await ws.send(json.dumps(sub))
                logger.info(f"Subscribed to {symbol} klines")

            heartbeat = asyncio.create_task(self._heartbeat(ws))
            try:
                async for msg in ws:
                    await self._handle(msg)
            finally:
                heartbeat.cancel()

    async def _heartbeat(self, ws):
        while True:
            await asyncio.sleep(15)
            try:
                await ws.send(json.dumps({"method": "ping"}))
            except Exception:
                break

    async def _handle(self, raw: str):
        try:
            msg = json.loads(raw)
        except Exception:
            return

        if msg.get("channel") != "push.kline":
            return

        symbol = msg.get("symbol", "")
        data   = msg.get("data", {})

        # Only process CLOSED candles
        if not data.get("end") and data.get("end") != 1:
            return

        ts = int(data.get("t", 0))
        if ts <= self._last_ts.get(symbol, 0):
            return
        self._last_ts[symbol] = ts

        candle = Candle(
            float(data.get("o", 0)),
            float(data.get("h", 0)),
            float(data.get("l", 0)),
            float(data.get("c", 0)),
            float(data.get("v", 0)),
            ts,
        )

        callback = self._callbacks.get(symbol)
        if callback:
            await callback(symbol, candle)
