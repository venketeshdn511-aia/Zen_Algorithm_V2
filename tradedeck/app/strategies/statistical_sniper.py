"""
Statistical Sniper Strategy — v2 Implementation
Mean reversion strategy using Z-Score extremes and KER filtering.
"""
import logging
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.db import OrderSide, OrderType, ProductType

logger = logging.getLogger(__name__)

class StatisticalSniper:
    def __init__(self, period=20, ker_threshold=0.30, z_entry=2.0, atr_period=14):
        self.period = period
        self.ker_threshold = ker_threshold
        self.z_entry = z_entry
        self.atr_period = atr_period
        
        # State Management
        self.position_state = None  # Simplified for one instance per symbol
        
    def _calculate_indicators(self, buffer) -> Optional[pd.DataFrame]:
        """Calculate Z-Score, KER, and ATR from tick buffer."""
        if len(buffer) < 50:
            return None
            
        df = pd.DataFrame(list(buffer))
        close = df["ltp"]
        
        # 1. Z-Score
        mean = close.rolling(window=self.period).mean()
        std = close.rolling(window=self.period).std()
        df["z_score"] = (close - mean) / std
        
        # 2. KER (Efficiency Ratio)
        change = (close - close.shift(self.period)).abs()
        volatility = close.diff().abs().rolling(window=self.period).sum()
        df["ker"] = (change / volatility).fillna(0)
        
        # 3. ATR
        # Since we only have LTP in buffer, we'll approximate TR as abs(close - prev_close)
        # Real ATR needs High/Low, but if buffer only has ticks, we use LTP delta.
        tr = close.diff().abs()
        df["atr"] = tr.rolling(window=self.atr_period).mean()
        
        return df

    async def __call__(self, tick: dict, buffer, db: AsyncSession, broker, risk) -> dict:
        """Main entrypoint called by StrategyExecutor."""
        df = self._calculate_indicators(buffer)
        if df is None:
            return {"signal": "WARMING_UP", "ltp": tick["ltp"]}
            
        curr = df.iloc[-1]
        z_score = curr["z_score"]
        ker = curr["ker"]
        atr = curr["atr"]
        ltp = tick["ltp"]
        
        # 1. MANAGE ACTIVE POSITION
        if self.position_state:
            state = self.position_state
            side = state["side"]
            entry = state["entry"]
            sl = state["sl"]
            t1 = state["t1"]
            stage = state["stage"]
            
            # Check Stop Loss
            if (side == "BUY" and ltp <= sl) or (side == "SELL" and ltp >= sl):
                logger.info(f"StatisticalSniper: SL HIT at {ltp} (Entry: {entry}, SL: {sl})")
                self.position_state = None
                return {"signal": "EXIT_SL", "ltp": ltp, "pnl": ltp - entry if side == "BUY" else entry - ltp}
                
            # Check T1 (Scale out 90%)
            if stage == 0:
                if (side == "BUY" and ltp >= t1) or (side == "SELL" and ltp <= t1):
                    logger.info(f"StatisticalSniper: T1 HIT at {ltp}. Scaling out 90%. Moving SL to BE.")
                    state["stage"] = 1
                    state["sl"] = entry # Move to Breakeven
                    # In real execution, we would close 90% here via broker
            
            # Trailing Stop (Stage 1+)
            if stage == 1:
                trail_dist = atr * 1.5
                if side == "BUY":
                    new_sl = ltp - trail_dist
                    if new_sl > state["sl"]: state["sl"] = new_sl
                else:
                    new_sl = ltp + trail_dist
                    if new_sl < state["sl"]: state["sl"] = new_sl

            return {
                "signal": "HOLDING",
                "ltp": ltp,
                "pnl": ltp - entry if side == "BUY" else entry - ltp,
                "open_qty": 50 if stage == 0 else 5, # 90% out
                "avg_entry": entry,
                "direction": side
            }

        # 2. CHECK FOR NEW ENTRY
        is_choppy = ker < self.ker_threshold
        signal = None
        if is_choppy and z_score < -self.z_entry:
            signal = "BUY"
        elif is_choppy and z_score > self.z_entry:
            signal = "SELL"
            
        if signal:
            risk_dist = atr * 2.0
            if risk_dist <= 0: risk_dist = ltp * 0.005
            
            sl = ltp - risk_dist if signal == "BUY" else ltp + risk_dist
            t1 = ltp + (risk_dist * 1.5) if signal == "BUY" else ltp - (risk_dist * 1.5)
            
            self.position_state = {
                "side": signal,
                "entry": ltp,
                "sl": sl,
                "t1": t1,
                "stage": 0,
                "target_instrument": {"type": "OPTION", "leg": "CE" if signal == "BUY" else "PE"}
            }
            logger.info(f"StatisticalSniper: SIGNAL {signal} at {ltp}, SL: {sl}, T1: {t1}")
            
            return {
                "signal": signal,
                "ltp": ltp,
                "avg_entry": ltp,
                "direction": signal,
                "target_instrument": {"type": "OPTION", "leg": "CE" if signal == "BUY" else "PE"}
            }

        return {
            "signal": "WAITING",
            "ltp": ltp,
            "z_score": round(z_score, 2),
            "ker": round(ker, 2)
        }

def get_strategy():
    return StatisticalSniper()
