"""
Failed Auction B2 Strategy — v2 Implementation
SHORTs the market on failed bullish breakouts in premium zones.
"""
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.db import OrderSide, OrderType, ProductType

logger = logging.getLogger(__name__)

class FailedAuctionB2:
    def __init__(self, rsi_period=14, lookback_period=20, range_period=50):
        self.rsi_period = rsi_period
        self.lookback_period = lookback_period
        self.range_period = range_period
        
        # State
        self.history_fetched = False
        self.candles_15m: List[Dict] = []
        self.current_candle: Optional[Dict] = None
        self.last_processed_minute: Optional[int] = None
        
        # Active trade
        self.active_order_id = None
        self.position = None

    def _update_candles(self, tick: dict):
        """Build 15m candles from incoming ticks."""
        tick_time = datetime.fromisoformat(tick["ts"])
        # Floor to 15m boundary
        candle_start = tick_time.replace(
            minute=(tick_time.minute // 15) * 15,
            second=0, microsecond=0
        )
        
        ltp = tick["ltp"]
        
        if self.current_candle is None or self.current_candle["time"] != candle_start:
            # New candle starts
            if self.current_candle:
                self.candles_15m.append(self.current_candle)
                # Keep only what we need for indicators
                if len(self.candles_15m) > 100:
                    self.candles_15m.pop(0)
            
            self.current_candle = {
                "time": candle_start,
                "open": ltp,
                "high": ltp,
                "low": ltp,
                "close": ltp,
                "volume": tick.get("vol", 0)
            }
        else:
            # Update current candle
            self.current_candle["high"] = max(self.current_candle["high"], ltp)
            self.current_candle["low"] = min(self.current_candle["low"], ltp)
            self.current_candle["close"] = ltp
            self.current_candle["volume"] += tick.get("vol", 0)

    def _calculate_indicators(self) -> Optional[pd.DataFrame]:
        """Convert collected candles to DataFrame and calc RSI/VWAP."""
        if len(self.candles_15m) < self.range_period:
            return None
        
        df = pd.DataFrame(self.candles_15m)
        # RSI
        delta = df["close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=self.rsi_period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.rsi_period).mean()
        rs = gain / loss
        df["rsi"] = 100 - (100 / (1 + rs))
        
        # VWAP (Simple day-wise)
        df["date"] = df["time"].dt.date
        df["tp"] = (df["high"] + df["low"] + df["close"]) / 3
        df["pv"] = df["tp"] * df["volume"]
        df["cum_pv"] = df.groupby("date")["pv"].cumsum()
        df["cum_vol"] = df.groupby("date")["volume"].cumsum()
        df["vwap"] = df["cum_pv"] / df["cum_vol"]
        
        return df

    async def __call__(self, tick: dict, buffer, db: AsyncSession, broker, risk) -> dict:
        """Main entrypoint called by StrategyExecutor."""
        if not self.history_fetched and broker:
            self.history_fetched = True
            symbol = tick.get("symbol")
            if symbol:
                try:
                    to_date = datetime.now()
                    from_date = to_date - timedelta(days=5)
                    
                    hist_data = await broker.get_history(
                        symbol=symbol,
                        resolution="15",
                        range_from=from_date.strftime("%Y-%m-%d"),
                        range_to=to_date.strftime("%Y-%m-%d")
                    )
                    
                    if hist_data and hist_data.get("s") == "ok" and "candles" in hist_data:
                        for candle in hist_data["candles"]:
                            # Fyers returns [epoch, open, high, low, close, volume]
                            dt = datetime.fromtimestamp(candle[0])
                            
                            self.candles_15m.append({
                                "time": dt,
                                "open": candle[1],
                                "high": candle[2],
                                "low": candle[3],
                                "close": candle[4],
                                "volume": candle[5]
                            })
                            
                        # Keep max 100 historical candles
                        if len(self.candles_15m) > 100:
                            self.candles_15m = self.candles_15m[-100:]
                            
                        logger.info(f"FailedAuctionB2: Fetched {len(hist_data['candles'])} historical 15m candles for {symbol}")
                except Exception as e:
                    logger.error(f"FailedAuctionB2: Error fetching historical data: {e}")

        self._update_candles(tick)
        
        df = self._calculate_indicators()
        if df is None:
            return {"signal": "WARMING_UP", "ltp": tick["ltp"], "thought_process": "Collecting candles for indicators, warming up..."}

        curr = df.iloc[-1]
        close_p = curr["close"]
        rsi = curr["rsi"]
        vwap = curr["vwap"]
        
        # 1. RSI Filter (40-60)
        if not (40 <= rsi <= 60):
            return {"signal": "RSI_OUT", "ltp": tick["ltp"], "rsi": round(rsi, 2), "thought_process": f"RSI is {rsi:.1f}, waiting for 40-60 zone"}
            
        # 2. VWAP Filter (Price > VWAP for bearish failure)
        if not (close_p > vwap):
            return {"signal": "BELOW_VWAP", "ltp": tick["ltp"], "vwap": round(vwap, 2), "thought_process": f"Price {close_p} is below VWAP {vwap:.1f}"}
        
        # 3. Premium Zone (Upper 50% of recent range)
        recent_df = df.iloc[-self.range_period:]
        r_high = recent_df["high"].max()
        r_low = recent_df["low"].min()
        r_mid = (r_high + r_low) / 2
        
        if not (close_p > r_mid):
            return {"signal": "NOT_PREMIUM", "ltp": tick["ltp"], "thought_process": f"Price {close_p} not in premium zone (> {r_mid:.1f})"}
            
        # 4. Failed Auction Logic (Sweep high and close below)
        past_df = df.iloc[-(self.lookback_period+1):-1]
        resistance = past_df["high"].max()
        
        swept = curr["high"] > resistance
        rejected = curr["close"] < resistance
        
        if swept and rejected and not self.position:
            # Signal: SHORT
            stop_loss = max(curr["high"], resistance)
            risk_amt = stop_loss - close_p
            if risk_amt <= 0: risk_amt = close_p * 0.001
            target = close_p - (risk_amt * 2.0)
            
            logger.info(f"FailedAuctionB2: SIGNAL SHORT at {close_p}, SL: {stop_loss}, TGT: {target}")
            
            return {
                "signal": "SELL",
                "ltp": close_p,
                "pnl": 0,
                "direction": "SHORT",
                "target_instrument": {"type": "OPTION", "leg": "PE"},
                "thought_process": f"Failed Auction confirmed! Sweep > {resistance:.1f} and rejection.",
                "stop_loss": stop_loss,
                "target_price": target
            }

        return {
            "signal": "WAITING",
            "ltp": tick["ltp"],
            "rsi": round(rsi, 2),
            "vwap": round(vwap, 2),
            "thought_process": f"Monitoring for sweep above {resistance:.1f}. RSI: {rsi:.1f}, VWAP: {vwap:.1f}"
        }

# Factory for the executor
def get_strategy():
    return FailedAuctionB2()
