"""
BB Squeeze Breakout Short Strategy (b9edd66f)
Logic:
1. Detect Bollinger Band Squeeze (contraction).
2. Bearish Breakout (Close < Lower Bollinger Band).
3. Exit Rules: 
   - Stop Loss (1.5x ATR from entry)
   - Take Profit (4.3x stop distance)
   - Price crosses VWAP opposite
"""
import pandas as pd
import numpy as np
from src.interfaces.strategy_interface import StrategyInterface

class BBSqueezeBreakoutStrategy(StrategyInterface):
    def __init__(self, bb_period=20, bb_std=2.0, atr_period=14, risk_reward=4.3):
        super().__init__("BBSqueezeBreakout_b9edd66f")
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.atr_period = atr_period
        self.risk_reward = risk_reward
        self.current_status = "Initializing..."
        self.last_signal_data = {}

    def get_status(self):
        return self.current_status

    def _calculate_vwap(self, df):
        """Calculate Intraday VWAP with fallback for zero volume"""
        df = df.copy()
        # Ensure column names are standardized for internal calculation
        df.columns = [c.lower() for c in df.columns]
        df['date'] = df.index.date
        df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3
        df['pv'] = df['typical_price'] * df['volume']
        df['cum_pv'] = df.groupby('date')['pv'].cumsum()
        df['cum_vol'] = df.groupby('date')['volume'].cumsum()
        # Fallback to Day's Open if volume is zero (common in historical index data)
        df['day_open'] = df.groupby('date')['open'].transform('first')
        return np.where(df['cum_vol'] > 0, df['cum_pv'] / (df['cum_vol'] + 1e-10), df['day_open'])

    def calculate_signal(self, df):
        if len(df) < 130: # Need 100 for quantile + 20 for BB + buffer
            self.current_status = f"Warming up ({len(df)}/130)"
            return None

        # Work on a copy and lowercase for consistency with backtest math
        df_calc = df.copy()
        df_calc.columns = [c.lower() for c in df_calc.columns]
        
        # 1. Bollinger Bands
        sma = df_calc['close'].rolling(window=self.bb_period).mean()
        std = df_calc['close'].rolling(window=self.bb_period).std()
        df_calc['bb_upper'] = sma + (self.bb_std * std)
        df_calc['bb_lower'] = sma - (self.bb_std * std)
        df_calc['bb_width'] = (df_calc['bb_upper'] - df_calc['bb_lower']) / sma
        
        # 2. ATR for SL
        tr1 = df_calc['high'] - df_calc['low']
        tr2 = (df_calc['high'] - df_calc['close'].shift(1)).abs()
        tr3 = (df_calc['low'] - df_calc['close'].shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df_calc['atr'] = tr.rolling(window=self.atr_period).mean()
        
        # 3. VWAP
        df_calc['vwap'] = self._calculate_vwap(df_calc)
        
        # 4. Squeeze Logic (Percentile-based, matching Code #1)
        # BB Width in bottom 50% of last 100 bars
        df_calc['bb_width_q50'] = df_calc['bb_width'].rolling(window=100).quantile(0.50)
        df_calc['is_squeezed'] = df_calc['bb_width'] <= df_calc['bb_width_q50']
        
        # Squeezed recently (last 12 bars)
        df_calc['was_squeezed_recently'] = df_calc['is_squeezed'].shift(1).rolling(window=12).max() > 0
        
        curr = df_calc.iloc[-1]
        prev = df_calc.iloc[-2]
        
        self.current_status = f"Scanning... Width:{curr['bb_width']:.4f} Q50:{curr['bb_width_q50']:.4f} Squeeze:{curr['is_squeezed']}"

        # EXIT Check (VWAP cross reversal)
        # Price crosses back above VWAP (Close > VWAP)
        if curr['close'] > curr['vwap'] and prev['close'] <= prev['vwap']:
            return 'exit_reversal'

        # ENTRY Check: Bearish Breakout during or after Squeeze
        is_breakout = curr['close'] <= curr['bb_lower']
        
        if is_breakout and (curr['is_squeezed'] or curr['was_squeezed_recently']):
            entry_p = curr['close']
            atr_val = curr['atr']
            
            # Stop loss: 1.5x ATR from entry (Matching Code #1)
            stop_loss = entry_p + (1.5 * atr_val)
            risk = stop_loss - entry_p
            
            if risk > 0:
                # Take profit: 4.3x stop distance
                target = entry_p - (risk * self.risk_reward)
                
                self.last_signal_data = {
                    'side': 'sell',
                    'entry': entry_p,
                    'stop_loss': stop_loss,
                    'take_profit': target,
                    'risk': risk,
                    'pattern': 'BB Squeeze Breakout (Synced)'
                }
                self.current_status = "🔴 SELL SIGNAL: Synced BB Squeeze Breakout"
                return 'sell'

        return None

        return None
