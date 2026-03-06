"""
Failed Auction B1 Strategy — v2 Implementation

B1 = BULLISH Failed Auction at SUPPORT (not B2 which is at resistance).

Logic:
  1. Price sweeps BELOW recent 20-bar support low (liquidity grab)
  2. Close recovers BACK ABOVE that support level → auction has FAILED BEARISH
  3. Market reverses UP → BUY a CE (call) option

Filters:
  - RSI in 40-65 zone (not oversold crash, not overbought)
  - Price below VWAP (weakness confirmed, then reversal valid)
  - Price in DISCOUNT zone (lower 50% of recent range)

Entry: Market CE buy on candle close above swept low
SL:    min(candle_low, support) 
TGT:   entry + 2 × risk (2:1 RR)

BUGS FIXED in this version:
  - tick["ts"] from Fyers WS is a float epoch (not ISO string) → handle both
  - VWAP divide-by-zero when volume=0 on early candles → guard added
  - position tracking: self.position flag prevents double-entry
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
import pandas as pd
import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.db import OrderSide, OrderType, ProductType

logger = logging.getLogger(__name__)

# NIFTY lot size
NIFTY_LOT_SIZE = 65


class FailedAuctionB1:
    def __init__(self, rsi_period=14, lookback_period=20, range_period=50):
        self.rsi_period      = rsi_period
        self.lookback_period = lookback_period
        self.range_period    = range_period

        # State
        self.history_fetched    = False
        self.candles_15m: List[Dict] = []
        self.current_candle: Optional[Dict] = None

        # Active trade tracking — prevents double-entry
        self.active_order_id = None
        self.position        = None   # "LONG" when in trade, None when flat

    def _parse_tick_time(self, tick: dict) -> datetime:
        """
        Fyers WS sends ts as float epoch OR ISO string depending on API version.
        Handle both gracefully.
        """
        ts_raw = tick.get("ts")
        try:
            if isinstance(ts_raw, (int, float)):
                return datetime.fromtimestamp(float(ts_raw), tz=timezone.utc)
            elif isinstance(ts_raw, str) and ts_raw:
                return datetime.fromisoformat(ts_raw)
        except Exception:
            pass
        return datetime.now(timezone.utc)

    def _update_candles(self, tick: dict):
        """Build 15m candles from incoming ticks."""
        tick_time = self._parse_tick_time(tick)
        candle_start = tick_time.replace(
            minute=(tick_time.minute // 15) * 15,
            second=0, microsecond=0
        )

        ltp = float(tick.get("ltp", 0))
        if ltp <= 0:
            return

        if self.current_candle is None or self.current_candle["time"] != candle_start:
            # New candle starts — archive previous
            if self.current_candle:
                self.candles_15m.append(self.current_candle)
                if len(self.candles_15m) > 120:
                    self.candles_15m.pop(0)

            self.current_candle = {
                "time":   candle_start,
                "open":   ltp,
                "high":   ltp,
                "low":    ltp,
                "close":  ltp,
                "volume": float(tick.get("vol", 0))
            }
        else:
            self.current_candle["high"]   = max(self.current_candle["high"], ltp)
            self.current_candle["low"]    = min(self.current_candle["low"], ltp)
            self.current_candle["close"]  = ltp
            self.current_candle["volume"] += float(tick.get("vol", 0))

    def _calculate_indicators(self) -> Optional[pd.DataFrame]:
        """Convert collected candles to DataFrame and calculate RSI/VWAP."""
        if len(self.candles_15m) < self.range_period:
            return None

        df = pd.DataFrame(self.candles_15m)

        # RSI
        delta = df["close"].diff()
        gain  = delta.where(delta > 0, 0).rolling(window=self.rsi_period).mean()
        loss  = (-delta.where(delta < 0, 0)).rolling(window=self.rsi_period).mean()
        rs    = gain / loss.replace(0, float("nan"))
        df["rsi"] = 100 - (100 / (1 + rs))

        # VWAP — guard against zero cumulative volume (pre-market / zero-vol candles)
        df["time"] = pd.to_datetime(df["time"])
        df["date"] = df["time"].dt.date
        df["tp"]   = (df["high"] + df["low"] + df["close"]) / 3
        df["pv"]   = df["tp"] * df["volume"]
        df["cum_pv"]  = df.groupby("date")["pv"].cumsum()
        df["cum_vol"] = df.groupby("date")["volume"].cumsum()
        df["vwap"]    = df["cum_pv"] / df["cum_vol"].replace(0, float("nan"))
        df["vwap"]    = df["vwap"].ffill()  # forward-fill any NaN from zero-vol candles

        return df

    async def __call__(self, tick: dict, buffer, db: AsyncSession, broker, risk) -> dict:
        """Main entrypoint called by StrategyExecutor on every tick."""

        # Fetch historical candles once at startup
        if not self.history_fetched and broker:
            self.history_fetched = True
            symbol = tick.get("symbol")
            if symbol:
                try:
                    to_date   = datetime.now()
                    from_date = to_date - timedelta(days=5)
                    hist_data = await broker.get_history(
                        symbol=symbol,
                        resolution="15",
                        range_from=from_date.strftime("%Y-%m-%d"),
                        range_to=to_date.strftime("%Y-%m-%d")
                    )
                    if hist_data and hist_data.get("s") == "ok" and "candles" in hist_data:
                        for candle in hist_data["candles"]:
                            dt = datetime.fromtimestamp(candle[0])
                            self.candles_15m.append({
                                "time":   dt,
                                "open":   candle[1],
                                "high":   candle[2],
                                "low":    candle[3],
                                "close":  candle[4],
                                "volume": candle[5]
                            })
                        if len(self.candles_15m) > 120:
                            self.candles_15m = self.candles_15m[-120:]
                        logger.info(
                            f"FailedAuctionB1: Fetched {len(hist_data['candles'])} historical 15m candles for {symbol}"
                        )
                except Exception as e:
                    logger.error(f"FailedAuctionB1: Error fetching historical data: {e}")

        self._update_candles(tick)

        df = self._calculate_indicators()
        if df is None:
            return {
                "signal": "WARMING_UP",
                "ltp": tick.get("ltp"),
                "thought_process": f"Collecting 15m candles... ({len(self.candles_15m)}/{self.range_period} ready)"
            }

        curr     = df.iloc[-1]
        close_p  = curr["close"]
        rsi      = curr["rsi"]
        vwap     = curr["vwap"]

        # ── FILTER 1: RSI in 40-65 (not oversold/overbought) ──────────────
        if not (40 <= rsi <= 65):
            return {
                "signal": "RSI_OUT", "ltp": tick.get("ltp"),
                "rsi": round(rsi, 2),
                "thought_process": f"RSI {rsi:.1f} outside 40-65 window — not in B1 zone"
            }

        # ── FILTER 2: Price below VWAP (weakness + reversal context valid) ─
        if close_p >= vwap:
            return {
                "signal": "ABOVE_VWAP", "ltp": tick.get("ltp"),
                "vwap": round(vwap, 2),
                "thought_process": f"Price {close_p:.1f} above VWAP {vwap:.1f} — B1 needs price BELOW VWAP first"
            }

        # ── FILTER 3: Discount zone (lower 50% of recent range) ───────────
        recent_df = df.iloc[-self.range_period:]
        r_high    = recent_df["high"].max()
        r_low     = recent_df["low"].min()
        r_mid     = (r_high + r_low) / 2

        if close_p >= r_mid:
            return {
                "signal": "NOT_DISCOUNT",
                "ltp": tick.get("ltp"),
                "thought_process": f"Price {close_p:.1f} not in discount zone (< {r_mid:.1f})"
            }

        # ── B1 CORE: Failed Bearish Auction ──────────────────────────────
        # 1. Find recent support (lowest low of past lookback candles)
        past_df  = df.iloc[-(self.lookback_period + 1):-1]
        support  = past_df["low"].min()

        # 2. Price swept BELOW support (liquidity grab)
        swept_below = curr["low"] < support
        # 3. Close recovered ABOVE support → auction failed bearish → BUY
        recovered   = curr["close"] > support

        if swept_below and recovered and not self.position:
            stop_loss = min(curr["low"], support) - (close_p * 0.001)  # tiny buffer
            risk_amt  = close_p - stop_loss
            if risk_amt <= 0:
                risk_amt = close_p * 0.001
            target = close_p + (risk_amt * 2.0)

            self.position = "LONG"  # Mark in-trade to prevent re-entry
            logger.info(
                f"FailedAuctionB1: SIGNAL BUY at {close_p:.2f}, "
                f"SL: {stop_loss:.2f}, TGT: {target:.2f}, support swept: {support:.2f}"
            )

            return {
                "signal":            "BUY",
                "ltp":               close_p,
                "pnl":               0,
                "direction":         "LONG",
                "open_qty":          NIFTY_LOT_SIZE,  # 75 — current NIFTY lot size
                "target_instrument": {"type": "OPTION", "leg": "CE"},  # Buy CE on bullish reversal
                "thought_process":   f"B1 CONFIRMED: Sweep below support {support:.1f}, close recovered above. RSI: {rsi:.1f}",
                "stop_loss":         stop_loss,
                "target_price":      target
            }

        # ── EXIT: SL or Target Hit (while in position) ────────────────────
        if self.position == "LONG":
            # Check if we've been tracking this trade (simple price-based exit)
            # For a real implementation, the executor tracks actual P&L
            # Here we signal exit when price moves adversely
            if close_p < (r_low - (r_low * 0.005)):
                self.position = None
                return {
                    "signal":    "EXIT_SL",
                    "ltp":       close_p,
                    "direction": "LONG",
                    "open_qty":  NIFTY_LOT_SIZE,
                    "thought_process": f"Stop loss hit below {r_low:.1f}"
                }

        return {
            "signal": "WAITING",
            "ltp":    tick.get("ltp"),
            "rsi":    round(rsi, 2),
            "vwap":   round(vwap, 2),
            "thought_process": f"Monitoring for sweep below {support:.1f}. RSI: {rsi:.1f}, VWAP: {vwap:.1f}, Position: {self.position or 'FLAT'}"
        }


# Factory for the executor
def get_strategy() -> FailedAuctionB1:
    return FailedAuctionB1()
