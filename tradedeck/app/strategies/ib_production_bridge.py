
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timezone, time
from typing import Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
import collections

# Import the user provided strategy class
# Note: initial_balance_breakout_strategy.py MUST be in the same directory
try:
    from app.strategies.initial_balance_breakout_strategy import InitialBalanceBreakoutStrategy
except ImportError as e:
    logging.error(f"IBBridge: Failed to import InitialBalanceBreakoutStrategy: {e}")
    raise

logger = logging.getLogger(__name__)

class IBProductionBridge:
    def __init__(self):
        # Instantiate the strategy with default parameters
        self.strategy = InitialBalanceBreakoutStrategy()
        
        # Internal state for candle aggregation
        self.candles_5m: List[Dict] = []
        self.current_candle: Optional[Dict] = None
        self.history_fetched = False
        
        # Mapping for bot compatibility
        self.last_ltp = 0.0

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
        """Build 5m candles from incoming ticks."""
        tick_time = self._parse_tick_time(tick)
        # Strategy expects IST-like time for IB logic (09:15-10:15)
        # We'll assume the system time or tick time is appropriate
        candle_start = tick_time.replace(
            minute=(tick_time.minute // 5) * 5,
            second=0, microsecond=0
        )

        ltp = float(tick.get("ltp", 0))
        if ltp <= 0:
            return False

        self.last_ltp = ltp
        new_candle_added = False
        
        if self.current_candle is None or self.current_candle["time"] != candle_start:
            if self.current_candle:
                self.candles_5m.append(self.current_candle)
                new_candle_added = True
                if len(self.candles_5m) > 200:
                    self.candles_5m.pop(0)

            self.current_candle = {
                "time":   candle_start,
                "Open":   ltp,
                "High":   ltp,
                "Low":    ltp,
                "Close":  ltp,
                "Volume": float(tick.get("vol", 0))
            }
        else:
            self.current_candle["High"]   = max(self.current_candle["High"], ltp)
            self.current_candle["Low"]    = min(self.current_candle["Low"], ltp)
            self.current_candle["Close"]  = ltp
            self.current_candle["Volume"] += float(tick.get("vol", 0))

        return new_candle_added

    async def __call__(self, tick: dict, buffer, db: AsyncSession, broker, risk) -> dict:
        """Main entrypoint for StrategyExecutor."""
        
        # 1. Fetch History once
        if not self.history_fetched and broker:
            self.history_fetched = True
            symbol = tick.get("symbol")
            if symbol:
                try:
                    to_date = datetime.now(timezone.utc)
                    from_date = to_date - pd.Timedelta(days=3)
                    hist_data = await broker.get_history(
                        symbol=symbol,
                        resolution="5",
                        range_from=from_date.strftime("%Y-%m-%d"),
                        range_to=to_date.strftime("%Y-%m-%d")
                    )
                    if hist_data and hist_data.get("s") == "ok":
                        for candle in hist_data.get("candles", []):
                            dt = datetime.fromtimestamp(candle[0], tz=timezone.utc)
                            self.candles_5m.append({
                                "time": dt,
                                "Open": candle[1],
                                "High": candle[2],
                                "Low": candle[3],
                                "Close": candle[4],
                                "Volume": candle[5]
                            })
                        logger.info(f"IBBridge: Loaded {len(self.candles_5m)} historical 5m candles")
                except Exception as e:
                    logger.error(f"IBBridge history fetch error: {e}")

        # 2. Update real-time candles
        self._update_candles(tick)
        
        # 3. Build DataFrame for strategy
        if not self.candles_5m:
            return {
                "signal": "WARMING_UP",
                "ltp": tick.get("ltp"),
                "thought_process": "Aggregating first 5m candle..."
            }
            
        # Strategy expects OHLCV with DatetimeIndex
        df = pd.DataFrame(self.candles_5m)
        df.set_index("time", inplace=True)
        # The strategy logic works on df.index.time and df.index.date
        
        # 4. Calculate Signal
        try:
            raw_signal = self.strategy.calculate_signal(df)
        except Exception as e:
            logger.error(f"IBBridge strategy error: {e}", exc_info=True)
            return {
                "signal": "ERROR",
                "ltp": tick.get("ltp"),
                "thought_process": f"Strategy calculation failed: {e}"
            }

        # 5. Map result
        status = self.strategy.get_status()
        
        # Standardize signal names
        signal_map = {"buy": "BUY", "sell": "SELL"}
        bot_signal = signal_map.get(raw_signal, "WAITING")
        
        # Extract signal metadata if any
        sig_data = self.strategy.last_signal_data or {}
        
        # For LIVE trading, we use the standard Nifty lot size from the executor
        # unless overridden. NIFTY lot is 50. 
        qty = 50 
        
        res = {
            "signal": bot_signal,
            "ltp": self.last_ltp,
            "direction": sig_data.get("side", "NEUTRAL").upper(),
            "open_qty": qty if bot_signal in ("BUY", "SELL") else 0,
            "thought_process": status,
            "target_instrument": {"type": "OPTION", "leg": "CE" if bot_signal == "BUY" else "PE"} if bot_signal in ("BUY", "SELL") else None,
            "stop_loss": sig_data.get("stop_loss"),
            "target_price": sig_data.get("take_profit")
        }
        
        return res

def get_strategy():
    return IBProductionBridge()
