"""
BB Squeeze Breakout Strategy — TradeDeck v2 Production Adapter
=============================================================
Strategy: BB Squeeze Breakout (b9edd66f)
Timeframe: 5-minute candles (built from live ticks)

Logic:
  1. Detect Bollinger Band Squeeze (BB Width in bottom 50% of last 100 bars)
  2. Bearish Breakout: Close <= Lower Bollinger Band during or within 12 bars after squeeze
  3. Exit Rules:
     - Stop Loss: 1.5x ATR above entry
     - Take Profit: 4.3x stop distance below entry
     - VWAP Reversal: Close crosses back above VWAP while short

This file implements the StrategyExecutor async callable interface:
    async def __call__(tick, buffer, db, broker, risk) -> dict
"""
import logging
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# NIFTY lot size (current as of 2025)
NIFTY_LOT_SIZE = 65

# Candle resolution: 5 minutes
CANDLE_RESOLUTION_MINUTES = 5


class BBSqueezeBreakoutStrategy:
    """
    BB Squeeze Breakout — Short-side momentum strategy.

    Detects volatility compressions via Bollinger Band squeeze, then
    enters a short on a bearish breakout below the Lower BB.
    The position is managed with ATR-based SL and VWAP reversal exit.
    """

    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 2.0,
        atr_period: int = 14,
        risk_reward: float = 4.3,
        squeeze_quantile: float = 0.50,
        squeeze_lookback: int = 100,
        squeeze_recency: int = 12,
        warmup_candles: int = 130,
    ):
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.atr_period = atr_period
        self.risk_reward = risk_reward
        self.squeeze_quantile = squeeze_quantile
        self.squeeze_lookback = squeeze_lookback
        self.squeeze_recency = squeeze_recency
        self.warmup_candles = warmup_candles

        # 5-minute candle state
        self._current_candle: Optional[dict] = None
        self._candles: list = []

        # Position tracking — prevents double-entry
        self._position: Optional[dict] = None  # None = flat, dict = in trade
        self._history_fetched: bool = False

    # ──────────────────────────────────────────────────────────────────────
    # Candle Builder (from live ticks)
    # ──────────────────────────────────────────────────────────────────────

    def _parse_tick_time(self, tick: dict) -> datetime:
        ts_raw = tick.get("ts")
        try:
            if isinstance(ts_raw, (int, float)):
                return datetime.fromtimestamp(float(ts_raw), tz=timezone.utc)
            elif isinstance(ts_raw, str) and ts_raw:
                return datetime.fromisoformat(ts_raw)
        except Exception:
            pass
        return datetime.now(timezone.utc)

    def _update_candles(self, tick: dict) -> bool:
        """
        Build 5-minute OHLCV candles from ticks.
        Returns True when a new candle is sealed (used to re-compute indicators).
        """
        tick_time = self._parse_tick_time(tick)
        bucket = tick_time.replace(
            minute=(tick_time.minute // CANDLE_RESOLUTION_MINUTES) * CANDLE_RESOLUTION_MINUTES,
            second=0,
            microsecond=0,
        )
        ltp = float(tick.get("ltp", 0))
        vol = float(tick.get("vol", tick.get("volume", 0)))

        if ltp <= 0:
            return False

        new_candle_sealed = False

        if self._current_candle is None or self._current_candle["time"] != bucket:
            # Seal the old candle
            if self._current_candle is not None:
                self._candles.append(self._current_candle)
                new_candle_sealed = True
                # Keep bounded: 300 candles ≈ 25 hours of 5m data
                if len(self._candles) > 300:
                    self._candles.pop(0)

            # Start fresh candle
            self._current_candle = {
                "time": bucket,
                "open": ltp,
                "high": ltp,
                "low": ltp,
                "close": ltp,
                "volume": vol,
            }
        else:
            c = self._current_candle
            c["high"] = max(c["high"], ltp)
            c["low"] = min(c["low"], ltp)
            c["close"] = ltp
            c["volume"] += vol

        return new_candle_sealed

    # ──────────────────────────────────────────────────────────────────────
    # Indicators
    # ──────────────────────────────────────────────────────────────────────

    def _calculate_vwap(self, df: pd.DataFrame) -> pd.Series:
        """Intraday VWAP with zero-volume guard (falls back to day open)."""
        df = df.copy()
        df["date"] = df["time"].dt.date
        df["tp"] = (df["high"] + df["low"] + df["close"]) / 3
        df["pv"] = df["tp"] * df["volume"]
        df["cum_pv"] = df.groupby("date")["pv"].cumsum()
        df["cum_vol"] = df.groupby("date")["volume"].cumsum()
        df["day_open"] = df.groupby("date")["open"].transform("first")
        return pd.Series(
            np.where(df["cum_vol"] > 0, df["cum_pv"] / (df["cum_vol"] + 1e-10), df["day_open"]),
            index=df.index,
        )

    def _build_indicators(self) -> Optional[pd.DataFrame]:
        """
        Compute Bollinger Bands, ATR, VWAP, and squeeze flags from sealed candles.
        Returns None if not enough candles for warmup.
        """
        if len(self._candles) < self.warmup_candles:
            return None

        df = pd.DataFrame(self._candles).copy()
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df = df.reset_index(drop=True)

        close = df["close"]

        # Bollinger Bands
        sma = close.rolling(self.bb_period).mean()
        std = close.rolling(self.bb_period).std()
        df["bb_upper"] = sma + self.bb_std * std
        df["bb_lower"] = sma - self.bb_std * std
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / sma

        # ATR (True Range)
        prev_close = close.shift(1)
        tr = pd.concat(
            [
                df["high"] - df["low"],
                (df["high"] - prev_close).abs(),
                (df["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        df["atr"] = tr.rolling(self.atr_period).mean()

        # VWAP
        df["vwap"] = self._calculate_vwap(df)

        # Squeeze: BB Width ≤ 50th-percentile of last 100 bars
        df["bb_width_q50"] = df["bb_width"].rolling(self.squeeze_lookback).quantile(self.squeeze_quantile)
        df["is_squeezed"] = df["bb_width"] <= df["bb_width_q50"]

        # Was squeezed recently (within last N bars, looking at prior bar)
        df["was_squeezed_recently"] = (
            df["is_squeezed"].shift(1).rolling(self.squeeze_recency).max() > 0
        )

        return df

    # ──────────────────────────────────────────────────────────────────────
    # Position Management
    # ──────────────────────────────────────────────────────────────────────

    def _check_exit(self, curr: pd.Series, prev: pd.Series, ltp: float) -> Optional[str]:
        """
        Check SL, TP, and VWAP reversal exit triggers.
        Returns exit signal string or None.
        """
        if self._position is None:
            return None

        pos = self._position
        entry = pos["entry"]
        sl = pos["stop_loss"]
        tp = pos["take_profit"]

        # Stop Loss: price closes above SL (short position)
        if curr["close"] >= sl:
            logger.info(
                f"BBSqueeze: SL HIT at {curr['close']:.2f} (Entry: {entry:.2f}, SL: {sl:.2f})"
            )
            return "EXIT_SL"

        # Take Profit: price closes at or below TP
        if curr["close"] <= tp:
            logger.info(
                f"BBSqueeze: TP HIT at {curr['close']:.2f} (Entry: {entry:.2f}, TP: {tp:.2f})"
            )
            return "EXIT_TP"

        # VWAP Reversal: price crosses back above VWAP while short
        if curr["close"] > curr["vwap"] and prev["close"] <= prev["vwap"]:
            logger.info(
                f"BBSqueeze: VWAP REVERSAL EXIT — Close {curr['close']:.2f} > VWAP {curr['vwap']:.2f}"
            )
            return "EXIT_VWAP"

        return None

    # ──────────────────────────────────────────────────────────────────────
    # Main Entrypoint (StrategyExecutor async callable interface)
    # ──────────────────────────────────────────────────────────────────────

    async def __call__(
        self, tick: dict, buffer, db: AsyncSession, broker, risk
    ) -> dict:
        """
        Called by StrategyExecutor on every tick for NSE:NIFTY50-INDEX.
        Returns a metrics dict consumed by _update_metrics().
        """
        # ── Hot-start: fetch historical 5m candles on first call ──────────
        if not self._history_fetched and broker:
            self._history_fetched = True
            symbol = tick.get("symbol")
            if symbol:
                try:
                    from datetime import timedelta
                    to_date = datetime.now(timezone.utc)
                    from_date = to_date - timedelta(days=7)
                    hist = await broker.get_history(
                        symbol=symbol,
                        resolution="5",
                        range_from=from_date.strftime("%Y-%m-%d"),
                        range_to=to_date.strftime("%Y-%m-%d"),
                    )
                    if hist and hist.get("s") == "ok" and "candles" in hist:
                        for c in hist["candles"]:
                            dt = datetime.fromtimestamp(c[0], tz=timezone.utc)
                            self._candles.append(
                                {
                                    "time": dt,
                                    "open": c[1],
                                    "high": c[2],
                                    "low": c[3],
                                    "close": c[4],
                                    "volume": c[5],
                                }
                            )
                        if len(self._candles) > 300:
                            self._candles = self._candles[-300:]
                        logger.info(
                            f"BBSqueezeBreakout: Hot-start loaded {len(self._candles)} historical 5m candles for {symbol}"
                        )
                except Exception as e:
                    logger.error(f"BBSqueezeBreakout: Error fetching historical data: {e}")

        # ── Build candle from tick ─────────────────────────────────────────
        new_candle_sealed = self._update_candles(tick)
        ltp = float(tick.get("ltp", 0))

        # Recompute indicators only on new sealed candle
        df = self._build_indicators()

        # ── Warmup phase ──────────────────────────────────────────────────
        if df is None:
            return {
                "signal": "WARMING_UP",
                "ltp": ltp,
                "thought_process": (
                    f"Warming up BB Squeeze... ({len(self._candles)}/{self.warmup_candles} 5m candles)"
                ),
            }

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        # ── EXIT MANAGEMENT (check before new entry) ──────────────────────
        exit_signal = self._check_exit(curr, prev, ltp)
        if exit_signal and self._position is not None:
            pos = self._position
            pnl_pts = pos["entry"] - curr["close"]  # Short PnL (positive if price fell)
            self._position = None

            return {
                "signal": exit_signal,
                "ltp": ltp,
                "pnl": round(pnl_pts * NIFTY_LOT_SIZE, 2),
                "direction": "SHORT",
                "open_qty": NIFTY_LOT_SIZE,
                "avg_entry": pos["entry"],
                "stop_loss": pos["stop_loss"],
                "target_price": pos["take_profit"],
                "target_instrument": {"type": "OPTION", "leg": "PE"},
                "thought_process": (
                    f"{exit_signal} @ {curr['close']:.2f} | "
                    f"Entry: {pos['entry']:.2f} | PnL pts: {pnl_pts:.2f}"
                ),
            }

        # ── HOLDING: update metrics while in position ─────────────────────
        if self._position is not None:
            pos = self._position
            pnl_pts = pos["entry"] - curr["close"]
            return {
                "signal": "HOLDING",
                "ltp": ltp,
                "pnl": round(pnl_pts * NIFTY_LOT_SIZE, 2),
                "direction": "SHORT",
                "open_qty": NIFTY_LOT_SIZE,
                "avg_entry": pos["entry"],
                "stop_loss": pos["stop_loss"],
                "target_price": pos["take_profit"],
                "target_instrument": {"type": "OPTION", "leg": "PE"},
                "thought_process": (
                    f"Holding SHORT | Entry: {pos['entry']:.2f} | "
                    f"LTP: {ltp:.2f} | SL: {pos['stop_loss']:.2f} | "
                    f"TP: {pos['take_profit']:.2f} | PnL pts: {pnl_pts:.2f}"
                ),
            }

        # ── ENTRY SCAN ────────────────────────────────────────────────────
        is_breakout = curr["close"] <= curr["bb_lower"]
        squeeze_active = curr["is_squeezed"] or curr["was_squeezed_recently"]

        atr = curr["atr"]
        bb_width = curr["bb_width"]
        q50 = curr["bb_width_q50"]

        if is_breakout and squeeze_active and not pd.isna(atr) and atr > 0:
            entry_p = curr["close"]
            stop_loss = entry_p + (1.5 * atr)
            risk_dist = stop_loss - entry_p
            take_profit = entry_p - (risk_dist * self.risk_reward)

            self._position = {
                "entry": entry_p,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
            }

            logger.info(
                f"BBSqueezeBreakout: 🔴 SELL SIGNAL | Entry: {entry_p:.2f} | "
                f"SL: {stop_loss:.2f} | TP: {take_profit:.2f} | ATR: {atr:.2f}"
            )

            return {
                "signal": "SELL",
                "ltp": ltp,
                "pnl": 0,
                "direction": "SHORT",
                "open_qty": NIFTY_LOT_SIZE,
                "avg_entry": entry_p,
                "stop_loss": stop_loss,
                "target_price": take_profit,
                "target_instrument": {"type": "OPTION", "leg": "PE"},
                "thought_process": (
                    f"BB Squeeze Breakout CONFIRMED 🔴 | "
                    f"Close {entry_p:.2f} <= LowerBB {curr['bb_lower']:.2f} | "
                    f"Width {bb_width:.4f} vs Q50 {q50:.4f} | ATR: {atr:.2f}"
                ),
            }

        # ── WAITING ───────────────────────────────────────────────────────
        squeeze_state = "SQUEEZED" if curr["is_squeezed"] else (
            "POST-SQUEEZE" if curr["was_squeezed_recently"] else "Normal"
        )
        return {
            "signal": "WAITING",
            "ltp": ltp,
            "thought_process": (
                f"Scanning | Squeeze: {squeeze_state} | "
                f"Width: {bb_width:.4f} Q50: {q50:.4f} | "
                f"LowerBB: {curr['bb_lower']:.2f} | LTP: {ltp:.2f}"
            ),
        }


# Factory function — matches the pattern used by all other strategies
def get_strategy() -> BBSqueezeBreakoutStrategy:
    return BBSqueezeBreakoutStrategy()
